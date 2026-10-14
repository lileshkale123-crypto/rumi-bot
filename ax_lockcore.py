"""Lock engine: which lock types a message breaks, plus the group=-4 watcher."""
import logging
import re
import time

from telegram import ChatPermissions
from telegram.constants import MessageEntityType as E
from telegram.ext import ApplicationHandlerStop, MessageHandler, filters

import ax_approve
from ax_common import esc, is_admin

log = logging.getLogger(__name__)

LOCK_TYPES = {
    "all": "Every message", "text": "Text messages", "photo": "Photos", "video": "Videos",
    "audio": "Audio files", "voice": "Voice notes", "videonote": "Round videos",
    "document": "Documents", "gif": "GIFs", "sticker": "Stickers", "poll": "Polls",
    "contact": "Contacts", "location": "Locations", "game": "Games", "forward": "Forwarded messages",
    "url": "Links", "email": "Emails", "phone": "Phone numbers", "command": "Commands",
    "button": "Messages with buttons", "invitelink": "Group/channel links", "botlink": "Bot links",
    "cashtag": "$CASHTAGS", "emoji": "Emoji", "emojionly": "Emoji-only messages",
    "emojigame": "Dice and darts", "cjk": "Chinese/Japanese/Korean text",
    "cyrillic": "Cyrillic text", "rtl": "Right-to-left text", "zalgo": "Glitchy text",
    "spoiler": "Spoilers", "bot": "Adding bots", "album": "Albums", "anonchannel": "Anonymous channel posts",
}

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\U0001F000-\U0001F2FF]")
_CJK = re.compile("[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_CYR = re.compile("[\u0400-\u04FF]")
_RTL = re.compile("[\u0590-\u08FF]")
_ZALGO = re.compile("[\u0300-\u036F]{3,}")
_INVITE = re.compile(r"(t\.me/|telegram\.me/)(joinchat|\+|[A-Za-z0-9_]{5,})", re.I)
_BOTLINK = re.compile(r"(t\.me/\w+bot\b|@\w+bot\b)", re.I)
_CASH = re.compile(r"\$[A-Z]{2,8}\b")
_PHONE = re.compile(r"\+?\d[\d\s\-]{8,14}\d")


def _entities(msg):
    return list(msg.entities or []) + list(msg.caption_entities or [])


def violations(msg):
    """Return the set of lock types this message breaks."""
    v = {"all"}
    text = msg.text or msg.caption or ""
    if msg.text:
        v.add("text")
    if msg.photo: v.add("photo")
    if msg.video: v.add("video")
    if msg.audio: v.add("audio")
    if msg.voice: v.add("voice")
    if msg.video_note: v.add("videonote")
    if msg.document and not msg.animation: v.add("document")
    if msg.animation: v.add("gif")
    if msg.sticker: v.add("sticker")
    if msg.poll: v.add("poll")
    if msg.contact: v.add("contact")
    if msg.location or msg.venue: v.add("location")
    if msg.game: v.add("game")
    if msg.dice: v.add("emojigame")
    if msg.forward_origin: v.add("forward")
    if msg.media_group_id: v.add("album")
    if msg.reply_markup and getattr(msg.reply_markup, "inline_keyboard", None): v.add("button")
    if msg.sender_chat and msg.sender_chat.id != msg.chat.id: v.add("anonchannel")
    kinds = {e.type for e in _entities(msg)}
    if kinds & {E.URL, E.TEXT_LINK}: v.add("url")
    if E.EMAIL in kinds: v.add("email")
    if E.BOT_COMMAND in kinds: v.add("command")
    if E.SPOILER in kinds: v.add("spoiler")
    if text:
        if _INVITE.search(text): v.add("invitelink")
        if _BOTLINK.search(text): v.add("botlink")
        if _CASH.search(text): v.add("cashtag")
        if _PHONE.search(text) or E.PHONE_NUMBER in kinds: v.add("phone")
        if _EMOJI.search(text):
            v.add("emoji")
            if not re.sub(r"[\s\u200d\ufe0f]|" + _EMOJI.pattern, "", text): v.add("emojionly")
        if _CJK.search(text): v.add("cjk")
        if _CYR.search(text): v.add("cyrillic")
        if _RTL.search(text): v.add("rtl")
        if _ZALGO.search(text): v.add("zalgo")
    if msg.new_chat_members and any(u.is_bot for u in msg.new_chat_members):
        v.add("bot")
    return v


_cfg = {}  # chat_id -> (time, locks dict, allowlist set, lockwarns)


async def load(pool, chat_id):
    hit = _cfg.get(chat_id)
    if hit and time.time() - hit[0] < 30:
        return hit[1], hit[2], hit[3]
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT lock_type, action, reason FROM ax_locks WHERE chat_id=$1", chat_id)
        allow = {r["item"].lower() for r in await c.fetch("SELECT item FROM ax_allowlist WHERE chat_id=$1", chat_id)}
        lw = await c.fetchval("SELECT lockwarns FROM ax_lock_settings WHERE chat_id=$1", chat_id) or False
    locks = {r["lock_type"]: (r["action"], r["reason"]) for r in rows}
    _cfg[chat_id] = (time.time(), locks, allow, lw)
    return locks, allow, lw


def invalidate(chat_id):
    _cfg.pop(chat_id, None)


def _allowed(msg, allow):
    if not allow:
        return False
    text = (msg.text or msg.caption or "").lower()
    if any(a in text for a in allow):
        return True
    fo = msg.forward_origin
    for attr in ("chat", "sender_user"):
        o = getattr(fo, attr, None)
        if o and (str(o.id) in allow or (getattr(o, "username", None) and "@" + o.username.lower() in allow)):
            return True
    return False


_admin_cache = {}


async def _is_admin_cached(bot, chat_id, uid):
    k = (chat_id, uid)
    hit = _admin_cache.get(k)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    ok = await is_admin(bot, chat_id, uid)
    _admin_cache[k] = (time.time(), ok)
    return ok


async def lock_watcher(update, context):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not user or user.is_bot:
        return
    pool = context.bot_data["pool"]
    locks, allow, lw = await load(pool, chat.id)
    if not locks:
        return
    hit = [t for t in violations(msg) if t in locks]
    if not hit:
        return
    if await ax_approve.is_approved(pool, chat.id, user.id) or await _is_admin_cached(context.bot, chat.id, user.id):
        return
    if _allowed(msg, allow):
        return
    action, reason = locks[hit[0]]
    try:
        await msg.delete()
    except Exception:
        return
    if action:
        try:
            import ax_actions
            await ax_actions.run(context, chat.id, user, action, reason or f"Locked: {hit[0]}")
        except Exception as e:
            log.warning("lock action failed: %s", e)
    elif lw:
        try:
            import ax_actions
            await ax_actions.run(context, chat.id, user, "warn", f"Locked: {hit[0]}")
        except Exception as e:
            log.warning("lockwarn failed: %s", e)
    raise ApplicationHandlerStop


def register(app):
    app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.PRIVATE & ~filters.COMMAND, lock_watcher), group=-4)

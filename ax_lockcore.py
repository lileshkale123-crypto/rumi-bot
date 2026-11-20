"""Lock engine: which lock types a message breaks, plus the outer lock middleware (aiogram 3.x)."""
import logging
import re
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.enums import MessageEntityType as E
from aiogram.types import Message, TelegramObject

import ax_approve
from ax_common import is_admin

log = logging.getLogger(__name__)

LOCK_TYPES = {
    "all": "Every message",
    "text": "Text messages",
    "photo": "Photos",
    "video": "Videos",
    "audio": "Audio files",
    "voice": "Voice notes",
    "videonote": "Round videos",
    "document": "Documents",
    "gif": "GIFs",
    "sticker": "Stickers",
    "poll": "Polls",
    "contact": "Contacts",
    "location": "Locations",
    "game": "Games",
    "forward": "Forwarded messages",
    "url": "Links",
    "email": "Emails",
    "phone": "Phone numbers",
    "command": "Commands",
    "button": "Messages with buttons",
    "invitelink": "Group/channel links",
    "botlink": "Bot links",
    "cashtag": "$CASHTAGS",
    "emoji": "Emoji",
    "emojionly": "Emoji-only messages",
    "emojigame": "Dice and darts",
    "cjk": "Chinese/Japanese/Korean text",
    "cyrillic": "Cyrillic text",
    "rtl": "Right-to-left text",
    "zalgo": "Glitchy text",
    "spoiler": "Spoilers",
    "bot": "Adding bots",
    "album": "Albums",
    "anonchannel": "Anonymous channel posts",
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

_cfg: dict[int, tuple[float, dict[str, tuple[str | None, str | None]], set[str], bool]] = {}
_CFG_MAX = 5000
_CFG_TTL = 30.0

_admin_cache: dict[tuple[int, int], tuple[float, bool]] = {}
_ADMIN_CACHE_MAX = 5000
_ADMIN_CACHE_TTL = 10.0

_registered = False


def _entities(msg: Message) -> list:
    return list(msg.entities or []) + list(msg.caption_entities or [])


def violations(msg: Message) -> set[str]:
    """Return the set of lock types this message breaks."""
    v = {"all"}
    text = msg.text or msg.caption or ""
    if msg.text:
        v.add("text")
    if msg.photo:
        v.add("photo")
    if msg.video:
        v.add("video")
    if msg.audio:
        v.add("audio")
    if msg.voice:
        v.add("voice")
    if msg.video_note:
        v.add("videonote")
    if msg.document and not msg.animation:
        v.add("document")
    if msg.animation:
        v.add("gif")
    if msg.sticker:
        v.add("sticker")
    if msg.poll:
        v.add("poll")
    if msg.contact:
        v.add("contact")
    if msg.location or msg.venue:
        v.add("location")
    if msg.game:
        v.add("game")
    if msg.dice:
        v.add("emojigame")
    if msg.forward_origin:
        v.add("forward")
    if msg.media_group_id:
        v.add("album")
    if msg.reply_markup and getattr(msg.reply_markup, "inline_keyboard", None):
        v.add("button")
    if msg.sender_chat and msg.sender_chat.id != msg.chat.id and not msg.is_automatic_forward:
        v.add("anonchannel")

    kinds = {e.type for e in _entities(msg)}
    if kinds & {E.URL, E.TEXT_LINK}:
        v.add("url")
    if E.EMAIL in kinds:
        v.add("email")
    if E.BOT_COMMAND in kinds:
        v.add("command")
    if E.SPOILER in kinds:
        v.add("spoiler")

    if text:
        if _INVITE.search(text):
            v.add("invitelink")
        if _BOTLINK.search(text):
            v.add("botlink")
        if _CASH.search(text):
            v.add("cashtag")
        if _PHONE.search(text) or E.PHONE_NUMBER in kinds:
            v.add("phone")
        if _EMOJI.search(text):
            v.add("emoji")
            if not re.sub(r"[\s\u200d\ufe0f]|" + _EMOJI.pattern, "", text):
                v.add("emojionly")
        if _CJK.search(text):
            v.add("cjk")
        if _CYR.search(text):
            v.add("cyrillic")
        if _RTL.search(text):
            v.add("rtl")
        if _ZALGO.search(text):
            v.add("zalgo")

    if msg.new_chat_members and any(u.is_bot for u in msg.new_chat_members):
        v.add("bot")
    return v


def _prune_cfg(now: float) -> None:
    if len(_cfg) <= _CFG_MAX:
        return
    expired = [cid for cid, (ts, _, _, _) in _cfg.items() if now - ts >= _CFG_TTL]
    for cid in expired:
        _cfg.pop(cid, None)
    if len(_cfg) > _CFG_MAX:
        oldest = sorted(_cfg.items(), key=lambda kv: kv[1][0])[: _CFG_MAX // 5]
        for cid, _ in oldest:
            _cfg.pop(cid, None)


def _prune_admin_cache(now: float) -> None:
    if len(_admin_cache) <= _ADMIN_CACHE_MAX:
        return
    expired = [k for k, (ts, _) in _admin_cache.items() if now - ts >= _ADMIN_CACHE_TTL]
    for k in expired:
        _admin_cache.pop(k, None)
    if len(_admin_cache) > _ADMIN_CACHE_MAX:
        oldest = sorted(_admin_cache.items(), key=lambda kv: kv[1][0])[: _ADMIN_CACHE_MAX // 5]
        for k, _ in oldest:
            _admin_cache.pop(k, None)


async def load(pool: asyncpg.Pool, chat_id: int):
    now = time.time()
    hit = _cfg.get(chat_id)
    if hit and now - hit[0] < _CFG_TTL:
        return hit[1], hit[2], hit[3]
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT lock_type, action, reason FROM ax_locks WHERE chat_id=$1",
            chat_id,
        )
        allow = {
            r["item"].lower()
            for r in await c.fetch("SELECT item FROM ax_allowlist WHERE chat_id=$1", chat_id)
        }
        lw = await c.fetchval(
            "SELECT lockwarns FROM ax_lock_settings WHERE chat_id=$1",
            chat_id,
        ) or False
    locks = {r["lock_type"]: (r["action"], r["reason"]) for r in rows}
    _prune_cfg(now)
    _cfg[chat_id] = (now, locks, allow, lw)
    return locks, allow, lw


def invalidate(chat_id: int) -> None:
    _cfg.pop(chat_id, None)


def _allowed(msg: Message, allow: set[str]) -> bool:
    if not allow:
        return False
    text = (msg.text or msg.caption or "").lower()
    if any(a in text for a in allow):
        return True
    fo = msg.forward_origin
    if fo is not None:
        for attr in ("chat", "sender_chat", "sender_user"):
            o = getattr(fo, attr, None)
            if o is not None:
                uname = getattr(o, "username", None)
                if str(o.id) in allow or (uname and "@" + uname.lower() in allow):
                    return True
    return False


async def _is_admin_cached(bot: Bot, chat_id: int, uid: int) -> bool:
    now = time.time()
    k = (chat_id, uid)
    hit = _admin_cache.get(k)
    if hit and now - hit[0] < _ADMIN_CACHE_TTL:
        return hit[1]
    ok = await is_admin(bot, chat_id, uid)
    _prune_admin_cache(now)
    _admin_cache[k] = (now, ok)
    return ok


async def check_locks(msg: Message, bot: Bot, pool: asyncpg.Pool) -> bool:
    """Returns True if message violated a lock and was deleted (stop further handling)."""
    chat = msg.chat
    user = msg.from_user
    if chat.type == "private":
        return False
    if msg.text and msg.text.startswith("/"):
        return False

    is_anon_channel = bool(
        msg.sender_chat and msg.sender_chat.id != chat.id and not msg.is_automatic_forward
    )
    if not is_anon_channel and (user is None or user.is_bot):
        return False

    locks, allow, lw = await load(pool, chat.id)
    if not locks:
        return False

    hit = [t for t in violations(msg) if t in locks]
    if not hit:
        return False

    if user is not None and not user.is_bot:
        if await ax_approve.is_approved(pool, chat.id, user.id) or await _is_admin_cached(
            bot, chat.id, user.id
        ):
            return False

    if _allowed(msg, allow):
        return False

    action, reason = locks[hit[0]]
    try:
        await msg.delete()
    except Exception:
        return False

    if user is not None and not user.is_bot:
        if action:
            try:
                import ax_actions

                await ax_actions.run(bot, pool, chat.id, user, action, reason or f"Locked: {hit[0]}")
            except Exception as e:
                log.warning("lock action failed: %s", e)
        elif lw:
            try:
                import ax_actions

                await ax_actions.run(bot, pool, chat.id, user, "warn", f"Locked: {hit[0]}")
            except Exception as e:
                log.warning("lockwarn failed: %s", e)
    return True


class LockMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                if await check_locks(event, bot, pool):
                    return None
        return await handler(event, data)


def register(dp) -> None:
    global _registered
    if _registered:
        return
    _registered = True
    dp.message.outer_middleware(LockMiddleware())

"""AFK status (long notes, stickers, GIFs, voice notes, photos, videos) and /info."""
import time
from datetime import datetime, timezone

from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from ax_common import esc, find_user_by_username, resolve_target

H = ParseMode.HTML
_notice = {}
MAXLEN = 1024        # longest AFK note (characters)
CAPTION = 1024       # Telegram's caption limit for GIF / voice / photo / video
TEXT_LIMIT = 4000    # Telegram's message limit is 4096
LABELS = {"sticker": "Sticker", "animation": "GIF", "voice": "Voice note",
          "photo": "Photo", "video": "Video"}


def dur(start):
    s = int((datetime.now(timezone.utc) - start).total_seconds())
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    parts = ([f"{h}h"] if h else []) + ([f"{m}m"] if m else []) + [f"{sec}s"]
    return " ".join(parts)


def media_of(m):
    """(type, file_id) of the first supported media in a message, else (None, None)."""
    if m is None:
        return None, None
    if m.sticker:
        return "sticker", m.sticker.file_id
    if m.animation:                 # GIFs (check before video)
        return "animation", m.animation.file_id
    if m.voice:
        return "voice", m.voice.file_id
    if m.photo:
        return "photo", m.photo[-1].file_id
    if m.video:
        return "video", m.video.file_id
    return None, None


def quote(inner, reason):
    """Long notes go in a collapsed (tap to expand) quote so the chat stays tidy."""
    tag = "blockquote expandable" if reason and len(reason) > 300 else "blockquote"
    return f"<{tag}>{inner}</blockquote>"


def fit(build, reason, limit):
    """build(reason) -> html. Shortens the reason until the whole text fits `limit`."""
    r = reason
    text = build(r)
    while len(text) > limit and r:
        r = r[: max(0, int(len(r) * 0.8) - 1)].rstrip()
        text = build((r + "…") if r else None)
    return text


def afk_text(name, start, reason):
    extra = f"\n📝 {esc(reason)}" if reason else ""
    return (f"💤 <b>{esc(name)} is AFK</b>\n"
            + quote(f"⏳ Away for <b>{dur(start)}</b>{extra}", reason))


_SEND = {
    "animation": lambda m, f, c: m.reply_animation(f, caption=c, parse_mode=H),
    "voice": lambda m, f, c: m.reply_voice(f, caption=c, parse_mode=H),
    "photo": lambda m, f, c: m.reply_photo(f, caption=c, parse_mode=H),
    "video": lambda m, f, c: m.reply_video(f, caption=c, parse_mode=H),
}


async def send_notice(msg, mtype, mid, name, start, reason):
    """Tell the group that someone is AFK, with their note and attached sticker/GIF/voice."""
    build = lambda r: afk_text(name, start, r)
    if mtype in _SEND and mid:
        try:                                   # media with the note as its caption
            await _SEND[mtype](msg, mid, fit(build, reason, CAPTION))
            return
        except Exception:
            pass                               # e.g. group blocks media: fall back to text
    elif mtype == "sticker" and mid:
        try:                                   # stickers can't have captions: sticker, then text
            await msg.reply_sticker(mid)
        except Exception:
            pass
    await msg.reply_text(fit(build, reason, TEXT_LIMIT), parse_mode=H)


_afk_ids = set()
_afk_loaded = False


async def _load_afk(pool):
    global _afk_loaded
    if _afk_loaded:
        return
    async with pool.acquire() as c:
        await c.execute("ALTER TABLE ax_afk ADD COLUMN IF NOT EXISTS media_type TEXT")
        await c.execute("ALTER TABLE ax_afk ADD COLUMN IF NOT EXISTS media_id TEXT")
        rows = await c.fetch("SELECT user_id FROM ax_afk")
    _afk_ids.update(r["user_id"] for r in rows)
    _afk_loaded = True


async def afk_cmd(update, context):
    user, msg = update.effective_user, update.effective_message
    pool = context.bot_data["pool"]
    try:
        await _load_afk(pool)
    except Exception as e:
        print("afk load error:", e)
    parts = (msg.text or "").split(None, 1)       # keep the line breaks of a long note
    arg = parts[1].strip() if len(parts) > 1 else ""
    if arg.lower() == "off":
        async with pool.acquire() as c:
            _afk_ids.discard(user.id); r = await c.execute("DELETE FROM ax_afk WHERE user_id=$1", user.id)
        await msg.reply_text(
            "✅ <b>AFK removed</b>" if r.endswith("1") else "💫 <b>You're not AFK</b>", parse_mode=H)
        return
    mtype, mid = media_of(msg.reply_to_message)   # reply to a sticker/GIF/voice to attach it
    reason = arg[:MAXLEN] or None
    _afk_ids.add(user.id)
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_afk (user_id, reason, media_type, media_id) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (user_id) DO UPDATE SET reason=$2, media_type=$3, media_id=$4, "
            "start_time=now(), msg_count=0", user.id, reason, mtype, mid)

    def build(r):
        extra = (f"\n📝 {esc(r)}" if r else "") + (f"\n📎 {LABELS[mtype]} attached" if mtype else "")
        return (f"💤 <b>AFK on</b>\n"
                + quote(f"👤 <b>{esc(user.first_name)}</b> is away{extra}", r))
    await msg.reply_text(fit(build, reason, TEXT_LIMIT), parse_mode=H)


async def afk_watcher(update, context):
    if update.edited_message:
        return  # no double replies on edits
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or user.is_bot:
        return
    pool = context.bot_data["pool"]
    raw = msg.text or msg.caption or ""
    try:
        await _load_afk(pool)
    except Exception as e:
        print("afk load error:", e)
        return
    if not _afk_ids:
        return
    if not raw.lower().startswith("/afk") and user.id in _afk_ids:
        _afk_ids.discard(user.id)
        async with pool.acquire() as c:
            r = await c.fetchrow(
                "DELETE FROM ax_afk WHERE user_id=$1 RETURNING reason, start_time, msg_count", user.id)
        if r:
            note = r["reason"] or ""
            short = note[:200] + ("…" if len(note) > 200 else "")
            extra = f"\n📝 {esc(short)}" if short else ""
            await msg.reply_text(
                f"👋 <b>Welcome back, {esc(user.first_name)}!</b>\n<blockquote>"
                f"⏳ Away for <b>{dur(r['start_time'])}</b>{extra}\n"
                f"💌 Mentions while away: <b>{r['msg_count']}</b></blockquote>", parse_mode=H)
    if raw.startswith("/"):
        return
    targets = set()
    rp = msg.reply_to_message
    if rp and rp.from_user and not rp.from_user.is_bot:
        targets.add(rp.from_user.id)
    su = getattr(getattr(msg, "forward_origin", None), "sender_user", None)
    if su and not su.is_bot:
        targets.add(su.id)
    for e in (msg.entities or msg.caption_entities or []):
        if e.type == "text_mention" and e.user and not e.user.is_bot:
            targets.add(e.user.id)
        elif e.type == "mention":
            name = (msg.parse_entity(e) if msg.text else msg.parse_caption_entity(e)).lstrip("@")
            row = await find_user_by_username(pool, name)
            if row:
                targets.add(row["id"])
    now = time.time()
    for tid in (targets & _afk_ids) - {user.id}:
        async with pool.acquire() as c:   # one round trip: count the mention and fetch everything
            r = await c.fetchrow(
                "WITH u AS (UPDATE ax_afk SET msg_count = msg_count + 1 WHERE user_id=$1 "
                "RETURNING reason, start_time, msg_count, media_type, media_id) "
                "SELECT u.reason, u.start_time, u.msg_count, u.media_type, u.media_id, "
                "(SELECT first_name FROM users WHERE id=$1) AS first_name FROM u", tid)
        if not r:
            continue
        key = (update.effective_chat.id, tid)
        if now - _notice.get(key, 0) < 15:
            continue
        _notice[key] = now
        await send_notice(msg, r["media_type"], r["media_id"], r["first_name"] or "This user",
                          r["start_time"], r["reason"])


# ===== /info and register =====
import config
import ui

ROLES = {"creator": "👑 Owner", "administrator": "🛡️ Admin", "member": "👤 Member",
         "restricted": "🚫 Restricted", "left": "🚪 Left", "kicked": "⛔ Banned"}


def ts(t):
    return t.strftime("%d %b %Y, %H:%M UTC") if t else "—"


async def info_cmd(update, context):
    msg, chat = update.effective_message, update.effective_chat
    pool = context.bot_data["pool"]
    target = await resolve_target(update, context, pool)
    if target is None:
        if context.args:
            await msg.reply_text(
                "🔎 <b>Not found</b>\n<blockquote>I haven't seen that person yet 💫</blockquote>", parse_mode=H)
            return
        target = update.effective_user
    user, role, title = target, "—", None
    if chat.type != "private":
        try:
            m = await context.bot.get_chat_member(chat.id, target.id)
            user, role = m.user, ROLES.get(m.status, "—")
            title = getattr(m, "custom_title", None)
        except Exception:
            pass
    async with pool.acquire() as c:
        st = await c.fetchrow(
            "SELECT first_seen, last_seen, message_count FROM ax_user_stats WHERE user_id=$1", target.id)
        w = await c.fetchrow("SELECT level, xp, cash, gems FROM wallets WHERE user_id=$1", target.id)
        afk = await c.fetchrow("SELECT reason, start_time FROM ax_afk WHERE user_id=$1", target.id)
    first = getattr(user, "first_name", None) or "User"
    name = esc(first + (" " + user.last_name if getattr(user, "last_name", None) else ""))
    uname = f"@{esc(user.username)}" if getattr(user, "username", None) else "—"
    kind = "🤖 Bot" if getattr(user, "is_bot", False) else "🧑 Human"
    text = (
        f"🪪 <b>User Info</b>\n<blockquote>"
        f"👤 <b>{name}</b>\n🔗 {uname}\n🆔 <code>{target.id}</code>\n{kind}\n"
        f"🎭 {role}" + (f" · 🏷️ {esc(title)}" if title else "") + "</blockquote>\n"
    )
    if w:
        text += (f"🌙 <b>Rumi Profile</b>\n<blockquote>⭐ Level <b>{w['level']}</b> · ✨ {w['xp']} XP\n"
                 f"💰 {config.CURRENCY_SYMBOL}{ui.format_number(w['cash'])} · 💎 {w['gems']}</blockquote>\n")
    if st:
        text += (f"📊 <b>Activity</b>\n<blockquote>💬 Messages: <b>{st['message_count']}</b>\n"
                 f"🌱 First seen: {ts(st['first_seen'])}\n🕒 Last seen: {ts(st['last_seen'])}</blockquote>\n")
    if afk:
        r = afk["reason"] or ""
        why = f" · {esc(r[:100])}{'…' if len(r) > 100 else ''}" if r else ""
        text += f"💤 <b>AFK</b> for {dur(afk['start_time'])}{why}"
    else:
        text += "🟢 <b>Active</b>"
    await msg.reply_text(text, parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("afk", afk_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.PRIVATE, afk_watcher), group=-2)

"""AFK status (long notes, stickers, GIFs, voice notes, photos, videos) and /info (aiogram 3.x)."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

import config
import ui
from ax_common import cmd_args, esc, find_user_by_username, resolve_target

log = logging.getLogger(__name__)
router = Router(name="ax_afk")

H = ParseMode.HTML
_notice: dict[tuple[int, int], float] = {}
_NOTICE_MAX = 5000

MAXLEN = 1024        # longest AFK note (characters)
CAPTION = 1024       # Telegram's caption limit for GIF / voice / photo / video
TEXT_LIMIT = 4000    # Telegram's message limit is 4096
LABELS = {
    "sticker": "Sticker",
    "animation": "GIF",
    "voice": "Voice note",
    "photo": "Photo",
    "video": "Video",
}

ROLES = {
    "creator": "👑 Owner",
    "administrator": "🛡️ Admin",
    "member": "👤 Member",
    "restricted": "🚫 Restricted",
    "left": "🚪 Left",
    "kicked": "⛔ Banned",
}

_afk_ids: set[int] = set()
_afk_loaded = False
_load_lock = asyncio.Lock()
_middleware_registered = False


def dur(start: datetime) -> str:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    s = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    parts = ([f"{h}h"] if h else []) + ([f"{m}m"] if m else []) + [f"{sec}s"]
    return " ".join(parts)


def media_of(m: Message | None) -> tuple[str | None, str | None]:
    """(type, file_id) of the first supported media in a message, else (None, None)."""
    if m is None:
        return None, None
    if m.sticker:
        return "sticker", m.sticker.file_id
    if m.animation:  # GIFs (check before video)
        return "animation", m.animation.file_id
    if m.voice:
        return "voice", m.voice.file_id
    if m.photo:
        return "photo", m.photo[-1].file_id
    if m.video:
        return "video", m.video.file_id
    return None, None


def quote(inner: str, reason: str | None) -> str:
    """Long notes go in a collapsed (tap to expand) quote so the chat stays tidy."""
    tag = "blockquote expandable" if reason and len(reason) > 300 else "blockquote"
    return f"<{tag}>{inner}</blockquote>"


def fit(build, reason: str | None, limit: int) -> str:
    """build(reason) -> html. Shortens the reason until the whole text fits `limit`."""
    r = reason
    text = build(r)
    while len(text) > limit and r:
        r = r[: max(0, int(len(r) * 0.8) - 1)].rstrip()
        text = build((r + "…") if r else None)
    return text


def afk_text(name: str, start: datetime, reason: str | None) -> str:
    extra = f"\n📝 {esc(reason)}" if reason else ""
    return (
        f"💤 <b>{esc(name)} is AFK</b>\n"
        + quote(f"⏳ Away for <b>{dur(start)}</b>{extra}", reason)
    )


_SEND = {
    "animation": lambda m, f, c: m.reply_animation(animation=f, caption=c, parse_mode=H),
    "voice": lambda m, f, c: m.reply_voice(voice=f, caption=c, parse_mode=H),
    "photo": lambda m, f, c: m.reply_photo(photo=f, caption=c, parse_mode=H),
    "video": lambda m, f, c: m.reply_video(video=f, caption=c, parse_mode=H),
}


async def send_notice(msg: Message, mtype: str | None, mid: str | None, name: str, start: datetime, reason: str | None) -> None:
    """Tell the group that someone is AFK, with their note and attached sticker/GIF/voice."""
    build = lambda r: afk_text(name, start, r)
    if mtype in _SEND and mid:
        try:  # media with the note as its caption
            await _SEND[mtype](msg, mid, fit(build, reason, CAPTION))
            return
        except Exception:
            pass  # e.g. group blocks media: fall back to text
    elif mtype == "sticker" and mid:
        try:  # stickers can't have captions: sticker, then text
            await msg.reply_sticker(sticker=mid)
        except Exception:
            pass
    await msg.reply(fit(build, reason, TEXT_LIMIT), parse_mode=H)


def _prune_notice(now: float) -> None:
    if len(_notice) <= _NOTICE_MAX:
        return
    stale = [k for k, ts_val in _notice.items() if now - ts_val >= 60.0]
    for k in stale:
        _notice.pop(k, None)
    if len(_notice) > _NOTICE_MAX:
        _notice.clear()


async def _load_afk(pool: asyncpg.Pool) -> None:
    global _afk_loaded
    if _afk_loaded:
        return
    async with _load_lock:
        if _afk_loaded:
            return
        async with pool.acquire() as c:
            await c.execute("ALTER TABLE ax_afk ADD COLUMN IF NOT EXISTS media_type TEXT")
            await c.execute("ALTER TABLE ax_afk ADD COLUMN IF NOT EXISTS media_id TEXT")
            rows = await c.fetch("SELECT user_id FROM ax_afk")
        _afk_ids.clear()
        _afk_ids.update(r["user_id"] for r in rows)
        _afk_loaded = True


@router.message(Command("afk"))
async def afk_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        await _load_afk(pool)
    except Exception as e:
        log.warning("afk load error: %s", e)

    parts = (message.text or "").split(None, 1)  # keep the line breaks of a long note
    arg = parts[1].strip() if len(parts) > 1 else ""
    if arg.lower() == "off":
        _afk_ids.discard(user.id)
        async with pool.acquire() as c:
            r = await c.execute("DELETE FROM ax_afk WHERE user_id=$1", user.id)
        await message.reply(
            "✅ <b>AFK removed</b>" if r.endswith("1") else "💫 <b>You're not AFK</b>",
            parse_mode=H,
        )
        return

    mtype, mid = media_of(message.reply_to_message)  # reply to a sticker/GIF/voice to attach it
    reason = arg[:MAXLEN] or None
    _afk_ids.add(user.id)
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_afk (user_id, reason, media_type, media_id) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (user_id) DO UPDATE SET reason=$2, media_type=$3, media_id=$4, "
            "start_time=now(), msg_count=0",
            user.id,
            reason,
            mtype,
            mid,
        )

    def build(r):
        extra = (f"\n📝 {esc(r)}" if r else "") + (f"\n📎 {LABELS[mtype]} attached" if mtype else "")
        return (
            "💤 <b>AFK on</b>\n"
            + quote(f"👤 <b>{esc(user.first_name or 'User')}</b> is away{extra}", r)
        )

    await message.reply(fit(build, reason, TEXT_LIMIT), parse_mode=H)


async def _handle_afk_watcher(msg: Message, pool: asyncpg.Pool) -> None:
    user = msg.from_user
    chat = msg.chat
    if user is None or user.is_bot or chat.type == "private":
        return

    raw = msg.text or msg.caption or ""
    try:
        await _load_afk(pool)
    except Exception as e:
        log.warning("afk load error: %s", e)
        return
    if not _afk_ids:
        return

    if not raw.lower().startswith("/afk") and user.id in _afk_ids:
        _afk_ids.discard(user.id)
        async with pool.acquire() as c:
            r = await c.fetchrow(
                "DELETE FROM ax_afk WHERE user_id=$1 RETURNING reason, start_time, msg_count",
                user.id,
            )
        if r:
            note = r["reason"] or ""
            short = note[:200] + ("…" if len(note) > 200 else "")
            extra = f"\n📝 {esc(short)}" if short else ""
            try:
                await msg.reply(
                    f"👋 <b>Welcome back, {esc(user.first_name or 'friend')}!</b>\n<blockquote>"
                    f"⏳ Away for <b>{dur(r['start_time'])}</b>{extra}\n"
                    f"💌 Mentions while away: <b>{r['msg_count']}</b></blockquote>",
                    parse_mode=H,
                )
            except Exception:
                pass

    if raw.startswith("/"):
        return

    targets: set[int] = set()
    rp = msg.reply_to_message
    if rp and rp.from_user and not rp.from_user.is_bot:
        targets.add(rp.from_user.id)

    su = getattr(getattr(msg, "forward_origin", None), "sender_user", None)
    if su and not su.is_bot:
        targets.add(su.id)

    ents = msg.entities if msg.text else (msg.caption_entities or [])
    src_text = msg.text if msg.text else (msg.caption or "")
    for e in ents or []:
        if e.type == "text_mention" and e.user and not e.user.is_bot:
            targets.add(e.user.id)
        elif e.type == "mention" and src_text:
            name = e.extract_from(src_text).lstrip("@")
            if name:
                row = await find_user_by_username(pool, name)
                if row:
                    targets.add(row["id"])

    now = time.time()
    _prune_notice(now)
    for tid in (targets & _afk_ids) - {user.id}:
        async with pool.acquire() as c:  # one round trip: count the mention and fetch everything
            r = await c.fetchrow(
                "WITH u AS (UPDATE ax_afk SET msg_count = msg_count + 1 WHERE user_id=$1 "
                "RETURNING reason, start_time, msg_count, media_type, media_id) "
                "SELECT u.reason, u.start_time, u.msg_count, u.media_type, u.media_id, "
                "(SELECT first_name FROM users WHERE id=$1) AS first_name FROM u",
                tid,
            )
        if not r:
            _afk_ids.discard(tid)
            continue
        key = (chat.id, tid)
        if now - _notice.get(key, 0.0) < 15.0:
            continue
        _notice[key] = now
        try:
            await send_notice(
                msg,
                r["media_type"],
                r["media_id"],
                r["first_name"] or "This user",
                r["start_time"],
                r["reason"],
            )
        except Exception:
            pass


class AfkMiddleware(BaseMiddleware):
    """Non-blocking watcher for AFK returns and AFK mentions in groups."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.type != "private":
            pool: asyncpg.Pool | None = data.get("pool")
            if pool is not None:
                await _handle_afk_watcher(event, pool)
        return await handler(event, data)


def ts(t: datetime | None) -> str:
    return t.strftime("%d %b %Y, %H:%M UTC") if t else "—"


@router.message(Command("info"))
async def info_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    me = message.from_user
    if me is None:
        return
    args = cmd_args(message)
    target = await resolve_target(message, bot, pool, args=args)
    if target is None:
        if args:
            await message.reply(
                "🔎 <b>Not found</b>\n<blockquote>I haven't seen that person yet 💫</blockquote>",
                parse_mode=H,
            )
            return
        target = me

    user, role, title = target, "—", None
    if chat.type != "private":
        try:
            m = await bot.get_chat_member(chat.id, target.id)
            user = m.user
            st_val = m.status.value if hasattr(m.status, "value") else str(m.status)
            role = ROLES.get(st_val, "—")
            title = getattr(m, "custom_title", None)
        except Exception:
            pass

    async with pool.acquire() as c:
        st = await c.fetchrow(
            "SELECT first_seen, last_seen, message_count FROM ax_user_stats WHERE user_id=$1",
            target.id,
        )
        w = await c.fetchrow(
            "SELECT level, xp, cash, gems FROM wallets WHERE user_id=$1",
            target.id,
        )
        afk = await c.fetchrow(
            "SELECT reason, start_time FROM ax_afk WHERE user_id=$1",
            target.id,
        )

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
        text += (
            f"🌙 <b>Rumi Profile</b>\n<blockquote>⭐ Level <b>{w['level']}</b> · ✨ {w['xp']} XP\n"
            f"💰 {config.CURRENCY_SYMBOL}{ui.format_number(w['cash'])} · 💎 {w['gems']}</blockquote>\n"
        )
    if st:
        text += (
            f"📊 <b>Activity</b>\n<blockquote>💬 Messages: <b>{st['message_count']}</b>\n"
            f"🌱 First seen: {ts(st['first_seen'])}\n🕒 Last seen: {ts(st['last_seen'])}</blockquote>\n"
        )
    if afk:
        r = afk["reason"] or ""
        why = f" · {esc(r[:100])}{'…' if len(r) > 100 else ''}" if r else ""
        text += f"💤 <b>AFK</b> for {dur(afk['start_time'])}{why}"
    else:
        text += "🟢 <b>Active</b>"
    await message.reply(text, parse_mode=H)


async def on_startup(bot: Bot, dispatcher) -> None:
    pool: asyncpg.Pool = dispatcher["pool"]
    await _load_afk(pool)


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(AfkMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

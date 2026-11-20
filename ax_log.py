"""Log channel support: send mod actions to a linked channel (aiogram 3.x)."""
import time

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from ax_common import cmd_args, esc, is_admin

router = Router(name="ax_log")
H = ParseMode.HTML
CATS = ("ban", "mute", "kick", "warn", "lock", "blocklist", "flood", "settings")

_cache: dict[int, tuple[float, int | None, set[str]]] = {}
_CACHE_TTL = 60.0
_CACHE_MAX = 5000


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX:
        return
    expired = [cid for cid, (ts, _, _) in _cache.items() if now - ts >= _CACHE_TTL]
    for cid in expired:
        _cache.pop(cid, None)
    if len(_cache) > _CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for cid, _ in oldest:
            _cache.pop(cid, None)


def invalidate(chat_id: int) -> None:
    _cache.pop(chat_id, None)


async def _row(pool: asyncpg.Pool, chat_id: int) -> tuple[int | None, set[str]]:
    now = time.time()
    hit = _cache.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1], set(hit[2])
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT log_chat, off_cats FROM ax_log_settings WHERE chat_id=$1",
            chat_id,
        )
    if not r:
        lc, off = None, set()
    else:
        lc = r["log_chat"]
        off = {x for x in (r["off_cats"] or "").split(",") if x}
    _prune_cache(now)
    _cache[chat_id] = (now, lc, set(off))
    return lc, off


async def send_log(bot: Bot, pool: asyncpg.Pool, chat_id: int, cat: str, text: str) -> None:
    """Send one log line. Never raises."""
    try:
        log_chat, off = await _row(pool, chat_id)
        if not log_chat or cat in off:
            return
        title = ""
        try:
            title = (await bot.get_chat(chat_id)).title or ""
        except Exception:
            pass
        await bot.send_message(
            log_chat,
            f"📋 <b>{esc(title)}</b> · <i>{cat}</i>\n<blockquote>{text}</blockquote>",
            parse_mode=H,
        )
    except Exception:
        pass


async def _gate(message: Message, bot: Bot, pool: asyncpg.Pool):
    chat = message.chat
    user = message.from_user
    if user is None:
        return None
    if chat.type == "private" or not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await message.reply(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>",
            parse_mode=H,
        )
        return None
    return pool, chat.id


@router.message(Command("setlog"))
async def setlog(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    target = None
    fwd = getattr(message, "forward_origin", None)
    if fwd is not None and getattr(fwd, "chat", None):
        target = fwd.chat.id
    elif message.reply_to_message:
        rfwd = getattr(message.reply_to_message, "forward_origin", None)
        if rfwd is not None and getattr(rfwd, "chat", None):
            target = rfwd.chat.id
    if target is None:
        args = cmd_args(message)
        if args:
            try:
                target = int(args[0])
            except ValueError:
                target = None
    if not target:
        await message.reply(
            "📋 <b>Set log channel</b>\n<blockquote>Use /setlog "
            "-100123... (channel id)\nThe bot must be admin there ✨</blockquote>",
            parse_mode=H,
        )
        return
    try:
        await bot.send_message(target, "✅ Log channel linked ✨")
    except Exception:
        await message.reply(
            "⚠️ <b>Cannot post there</b>\n<blockquote>Add me as an admin in that channel first ✨</blockquote>",
            parse_mode=H,
        )
        return
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_log_settings (chat_id, log_chat) VALUES "
            "($1,$2) ON CONFLICT (chat_id) DO UPDATE SET log_chat=$2",
            cid,
            target,
        )
    invalidate(cid)
    await message.reply("✅ <b>Log channel set</b>", parse_mode=H)


@router.message(Command("unsetlog"))
async def unsetlog(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    async with pool.acquire() as c:
        await c.execute("UPDATE ax_log_settings SET log_chat=NULL WHERE chat_id=$1", cid)
    invalidate(cid)
    await message.reply("🚫 <b>Log channel removed</b>", parse_mode=H)


@router.message(Command("logchannel"))
async def logchannel(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    lc, off = await _row(pool, cid)
    await message.reply(
        f"📋 <b>Log channel</b>\n<blockquote>Linked: "
        f"<b>{lc or 'none'}</b>\nOff: <b>{', '.join(sorted(off)) or 'none'}</b></blockquote>",
        parse_mode=H,
    )


def _toggle(turn_on: bool):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        g = await _gate(message, bot, pool)
        if not g:
            return
        pool, cid = g
        args = cmd_args(message)
        a = args[0].lower() if args else ""
        if a not in CATS:
            await message.reply("<blockquote>Pick one: " + ", ".join(CATS) + "</blockquote>", parse_mode=H)
            return
        _, off = await _row(pool, cid)
        if turn_on:
            off.discard(a)
        else:
            off.add(a)
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_log_settings (chat_id, off_cats) VALUES "
                "($1,$2) ON CONFLICT (chat_id) DO UPDATE SET off_cats=$2",
                cid,
                ",".join(sorted(off)),
            )
        invalidate(cid)
        await message.reply(
            f"{'✅' if turn_on else '🚫'} <b>{a} logs {'on' if turn_on else 'off'}</b>",
            parse_mode=H,
        )

    return cmd


@router.message(Command("logcategories"))
async def logcategories(message: Message) -> None:
    await message.reply(
        "📋 <b>Log categories</b>\n<blockquote>" + "\n".join(CATS) + "</blockquote>",
        parse_mode=H,
    )


router.message(Command("log"))(_toggle(True))
router.message(Command("nolog"))(_toggle(False))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

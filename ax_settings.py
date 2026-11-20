"""Flood protection and the settings menu (aiogram 3.x)."""
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject

import ax_welcome
from ax_common import btn, cmd_args, fire, is_admin

router = Router(name="ax_settings")
H = ParseMode.HTML

_cache: dict[int, tuple[float, tuple[int, int, int]]] = {}
_CACHE_MAX = 5000
_hits: dict[tuple[int, int], list[float]] = {}
_HITS_MAX = 10_000
_middleware_registered = False


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX:
        return
    stale = [cid for cid, (ts, _) in _cache.items() if now - ts >= 120.0]
    for cid in stale:
        _cache.pop(cid, None)
    if len(_cache) > _CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for cid, _ in oldest:
            _cache.pop(cid, None)


def _prune_hits(now: float) -> None:
    if len(_hits) <= _HITS_MAX:
        return
    stale = [k for k, lst in _hits.items() if not lst or now - lst[-1] >= 60.0]
    for k in stale:
        _hits.pop(k, None)
    if len(_hits) > _HITS_MAX:
        _hits.clear()


async def _cfg_load(pool: asyncpg.Pool, chat_id: int) -> tuple[int, int, int]:
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT flood_limit, flood_window, flood_mute FROM ax_settings WHERE chat_id=$1",
            chat_id,
        )
    v = (int(r["flood_limit"]), int(r["flood_window"]), int(r["flood_mute"])) if r else (0, 5, 300)
    now = time.time()
    _prune_cache(now)
    _cache[chat_id] = (now, v)
    return v


async def cfg(pool: asyncpg.Pool, chat_id: int) -> tuple[int, int, int]:
    c = _cache.get(chat_id)
    if c:
        now = time.time()
        if now - c[0] >= 30.0:
            _cache[chat_id] = (now, c[1])  # only one refresh at a time
            fire(_cfg_load(pool, chat_id))
        return c[1]
    return await _cfg_load(pool, chat_id)


async def set_limit(pool: asyncpg.Pool, chat_id: int, n: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ax_settings (chat_id, flood_limit) VALUES ($1,$2) "
            "ON CONFLICT (chat_id) DO UPDATE SET flood_limit=$2",
            chat_id,
            n,
        )
    _cache.pop(chat_id, None)


async def _check_flood(msg: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = msg.chat
    user = msg.from_user
    if chat.type == "private" or user is None or user.is_bot:
        return
    limit, window, mute = await cfg(pool, chat.id)
    if limit == 0:
        return
    now = time.time()
    _prune_hits(now)
    key = (chat.id, user.id)
    hits = [t for t in _hits.get(key, []) if now - t < window] + [now]
    _hits[key] = hits
    if len(hits) < limit:
        return
    _hits[key] = []

    if await is_admin(bot, chat.id, user.id):
        return
    import ax_approve

    if await ax_approve.is_approved(pool, chat.id, user.id):
        return
    try:
        import ax_flood

        await ax_flood.punish(bot, pool, chat, user, msg, mute)
    except Exception:
        pass


class FloodWatcherMiddleware(BaseMiddleware):
    """Non-blocking antiflood observer on group messages."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.type != "private":
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                await _check_flood(event, bot, pool)
        return await handler(event, data)


def nav(*rows) -> InlineKeyboardMarkup:
    home = [btn("⬅ Back", "axs:main"), btn("🏠 Home", "axs:main")]
    return InlineKeyboardMarkup(inline_keyboard=[*rows, home])


def main_view() -> tuple[str, InlineKeyboardMarkup]:
    text = "⚙️ <b>Rumi Settings</b>\n<blockquote>Pick a category to customize your group 💫</blockquote>"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🌊 Flood Protection", "axs:flood", style="primary")],
            [
                btn("👋 Welcome", "axs:welcome", style="primary"),
                btn("😢 Goodbye", "axs:goodbye", style="primary"),
            ],
            [btn("✖ Close", "axs:close", style="danger")],
        ]
    )
    return text, kb


async def flood_view(pool: asyncpg.Pool, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    limit, window, mute = await cfg(pool, chat_id)
    status = "🔴 Off" if limit == 0 else f"🟢 On: {limit} msgs / {window}s, mute {mute // 60}m"
    text = f"🌊 <b>Flood Protection</b>\n<blockquote>Status: <b>{status}</b>\nCustom value: /setflood 8</blockquote>"
    return text, nav(
        [
            btn("🟢 On (6)", "axs:flood:on:6", style="success"),
            btn("🟢 On (10)", "axs:flood:on:10", style="success"),
        ],
        [btn("🔴 Turn off", "axs:flood:off", style="danger")],
    )


async def msg_view(pool: asyncpg.Pool, chat_id: int, kind: str) -> tuple[str, InlineKeyboardMarkup]:
    t, e, p = await ax_welcome.get(pool, chat_id, kind)
    icon = "👋" if kind == "welcome" else "😢"
    text = (
        f"{icon} <b>{kind.title()} Message</b>\n<blockquote>Status: <b>{'🟢 On' if e else '🔴 Off'}</b>\n"
        f"Text: <b>{'custom' if t else 'default'}</b>\nPhoto: <b>{'custom' if p else 'default'}</b>\n"
        f"Customize: /set{kind}</blockquote>"
    )
    return text, nav(
        [
            btn("🟢 Enable", f"axs:{kind}:on", style="success"),
            btn("🔴 Disable", f"axs:{kind}:off", style="danger"),
        ],
        [btn("🔄 Reset to default", f"axs:{kind}:reset", style="primary")],
    )


@router.message(Command("settings"))
async def settings_cmd(message: Message, bot: Bot) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type == "private" or not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await message.reply(
            "🛡️ <b>Admins only</b>\n<blockquote>Open this in your group as an admin ✨</blockquote>",
            parse_mode=H,
        )
        return
    text, kb = main_view()
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.message(Command("setflood"))
async def setflood_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type == "private" or not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await message.reply(
            "🛡️ <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>",
            parse_mode=H,
        )
        return
    args = cmd_args(message)
    a = args[0].lower() if args else ""
    if a == "off":
        await set_limit(pool, chat.id, 0)
        await message.reply("🔴 <b>Flood protection off</b>", parse_mode=H)
    elif a.isdigit() and int(a) >= 3:
        await set_limit(pool, chat.id, int(a))
        limit, window, _ = await cfg(pool, chat.id)
        await message.reply(
            f"🟢 <b>Flood protection on</b>\n<blockquote>{limit} messages in {window}s triggers a mute 💫</blockquote>",
            parse_mode=H,
        )
    else:
        text, kb = await flood_view(pool, chat.id)
        await message.reply(text, parse_mode=H, reply_markup=kb)


@router.callback_query(F.data.startswith("axs:"))
async def settings_callback(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if query.message is None or not query.data:
        await query.answer()
        return
    chat = query.message.chat
    if not await is_admin(bot, chat.id, query.from_user.id, "can_change_info"):
        await query.answer("Admins only 🛡️", show_alert=True)
        return
    p = query.data.split(":")
    sec = p[1] if len(p) > 1 else "main"
    act = p[2] if len(p) > 2 else None

    if sec == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if sec == "flood":
        if act == "on" and len(p) > 3 and p[3].isdigit():
            await set_limit(pool, chat.id, int(p[3]))
        elif act == "off":
            await set_limit(pool, chat.id, 0)
        text, kb = await flood_view(pool, chat.id)
    elif sec in ("welcome", "goodbye"):
        if act in ("on", "off"):
            await ax_welcome.put(pool, chat.id, sec, "enabled", act == "on")
        elif act == "reset":
            await ax_welcome.put(pool, chat.id, sec, "text", None)
            await ax_welcome.put(pool, chat.id, sec, "photo", None)
        text, kb = await msg_view(pool, chat.id, sec)
    else:
        text, kb = main_view()

    await query.answer("Done ✨" if act else None)
    try:
        await query.message.edit_text(text, parse_mode=H, reply_markup=kb)
    except Exception:
        pass


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(FloodWatcherMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

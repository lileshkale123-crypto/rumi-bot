"""Flood extras: /flood /setfloodtimer /floodmode /clearflood + punishment used by flood watcher (aiogram 3.x)."""
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

import ax_actions
import ax_mod
from ax_common import cmd_args, is_admin
from ax_warn2 import fmt_time, parse_time

router = Router(name="ax_flood")

MODES = ("ban", "mute", "kick", "tban", "tmute")
_recent: dict[tuple[int, int], list[tuple[float, int]]] = {}  # (chat_id, user_id) -> [(time, message_id)]
_RECENT_MAX = 10_000
_middleware_registered = False


def _prune_recent(now: float) -> None:
    if len(_recent) <= _RECENT_MAX:
        return
    stale = [k for k, lst in _recent.items() if not lst or now - lst[-1][0] >= 120.0]
    for k in stale:
        _recent.pop(k, None)
    if len(_recent) > _RECENT_MAX:
        oldest = sorted(_recent.items(), key=lambda kv: kv[1][-1][0] if kv[1] else 0.0)[: _RECENT_MAX // 5]
        for k, _ in oldest:
            _recent.pop(k, None)


def record_message(msg: Message) -> None:
    chat = msg.chat
    user = msg.from_user
    if chat.type == "private" or user is None or user.is_bot:
        return
    k = (chat.id, user.id)
    now = time.time()
    _prune_recent(now)
    lst = [x for x in _recent.get(k, []) if now - x[0] < 120.0][-49:]
    lst.append((now, msg.message_id))
    _recent[k] = lst


class FloodRecordMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            record_message(event)
        return await handler(event, data)


async def get(pool: asyncpg.Pool, chat_id: int) -> dict:
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT flood_limit, flood_window, flood_mute, flood_mode, flood_mode_secs, flood_clear "
            "FROM ax_settings WHERE chat_id=$1",
            chat_id,
        )
    if r:
        return dict(r)
    return {
        "flood_limit": 0,
        "flood_window": 5,
        "flood_mute": 300,
        "flood_mode": "tmute",
        "flood_mode_secs": 0,
        "flood_clear": False,
    }


async def punish(bot: Bot, pool: asyncpg.Pool, chat, user, msg: Message, mute_secs: int) -> None:
    s = await get(pool, chat.id)
    if s["flood_clear"]:
        raw_ids = [m for _, m in _recent.pop((chat.id, user.id), [])]
        if msg.message_id not in raw_ids:
            raw_ids.append(msg.message_id)
        # Deduplicate while preserving order and delete in Telegram's max 100-ID batches
        ids = list(dict.fromkeys(raw_ids))
        for i in range(0, len(ids), 100):
            try:
                await bot.delete_messages(chat.id, ids[i : i + 100])
            except Exception:
                pass
    mode, secs = s["flood_mode"], s["flood_mode_secs"]
    if mode in ("tban", "tmute"):
        action = f"{mode} {secs or (mute_secs if mode == 'tmute' else 3600)}s"
    else:
        action = mode
    await ax_actions.run(bot, pool, chat.id, user, action, "Flooding the chat")


async def _gate(message: Message, bot: Bot) -> bool:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>",
            bot=bot,
        )
        return False
    if not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
            bot=bot,
        )
        return False
    return True


def _invalidate_settings_cache(chat_id: int) -> None:
    try:
        import ax_settings

        ax_settings._cache.pop(chat_id, None)
    except Exception:
        pass


@router.message(Command("flood"))
async def flood_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    s = await get(pool, message.chat.id)
    if s["flood_limit"] == 0:
        await ax_mod.say(
            message,
            "🌊 <b>Antiflood is off</b>\n<blockquote>Turn on: /setflood 6 (messages) or /setfloodtimer 10 30s</blockquote>",
            bot=bot,
        )
        return
    mode = s["flood_mode"]
    if mode in ("tban", "tmute"):
        mode += f" {fmt_time(s['flood_mode_secs'] or s['flood_mute'])}"
    await ax_mod.say(
        message,
        (
            "🌊 <b>Antiflood</b>\n<blockquote>"
            f"📊 <b>Trigger:</b> {s['flood_limit']} messages in {s['flood_window']}s\n"
            f"🔨 <b>Action:</b> {mode}\n"
            f"🧹 <b>Delete flood:</b> {'on' if s['flood_clear'] else 'off'}</blockquote>"
        ),
        bot=bot,
    )


@router.message(Command("setfloodtimer"))
async def setfloodtimer_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = cmd_args(message)
    if args and args[0].lower() in ("off", "no"):
        import ax_settings

        await ax_settings.set_limit(pool, chat_id, 0)
        await ax_mod.say(
            message,
            "🌊 <b>Antiflood off</b>\n<blockquote>No flood checks now</blockquote>",
            bot=bot,
        )
        return
    secs = parse_time(args[1]) if len(args) > 1 else None
    if len(args) < 2 or not args[0].isdigit() or int(args[0]) < 3 or not secs or secs > 600:
        await ax_mod.say(
            message,
            "💫 <b>Usage</b>\n<blockquote>/setfloodtimer 10 30s: 10 messages within 30 seconds\n/setfloodtimer off</blockquote>",
            bot=bot,
        )
        return
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_settings (chat_id, flood_limit, flood_window) VALUES ($1,$2,$3) "
            "ON CONFLICT (chat_id) DO UPDATE SET flood_limit=$2, flood_window=$3",
            chat_id,
            int(args[0]),
            secs,
        )
    _invalidate_settings_cache(chat_id)
    await ax_mod.say(
        message,
        f"✅ <b>Timed antiflood set</b>\n<blockquote>{args[0]} messages within {fmt_time(secs)} triggers it 🌊</blockquote>",
        bot=bot,
    )


@router.message(Command("floodmode"))
async def floodmode_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = [a.lower() for a in cmd_args(message)]
    if not args or args[0] not in MODES:
        s = await get(pool, chat_id)
        await ax_mod.say(
            message,
            f"⚙️ <b>Flood action</b>\n<blockquote>Currently <b>{s['flood_mode']}</b>\n"
            "Set: /floodmode ban, mute, kick, tban or tmute (example: /floodmode tmute 3h)</blockquote>",
            bot=bot,
        )
        return
    secs = 0
    if args[0] in ("tban", "tmute") and len(args) > 1:
        secs = parse_time(args[1])
        if not secs:
            await ax_mod.say(
                message,
                "⏳ <b>Bad time</b>\n<blockquote>Example: /floodmode tmute 3h</blockquote>",
                bot=bot,
            )
            return
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_settings (chat_id, flood_mode, flood_mode_secs) VALUES ($1,$2,$3) "
            "ON CONFLICT (chat_id) DO UPDATE SET flood_mode=$2, flood_mode_secs=$3",
            chat_id,
            args[0],
            secs,
        )
    _invalidate_settings_cache(chat_id)
    await ax_mod.say(
        message,
        f"✅ <b>Flood action set</b>\n<blockquote>Flooders get <b>{args[0]}</b></blockquote>",
        bot=bot,
    )


@router.message(Command("clearflood"))
async def clearflood_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = cmd_args(message)
    arg = args[0].lower() if args else ""
    if arg not in ("on", "off", "yes", "no"):
        s = await get(pool, chat_id)
        await ax_mod.say(
            message,
            f"⚙️ <b>Delete flood</b>\n<blockquote>Currently <b>{'on' if s['flood_clear'] else 'off'}</b>\n"
            "Use /clearflood on or off</blockquote>",
            bot=bot,
        )
        return
    val = arg in ("on", "yes")
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_settings (chat_id, flood_clear) VALUES ($1,$2) "
            "ON CONFLICT (chat_id) DO UPDATE SET flood_clear=$2",
            chat_id,
            val,
        )
    _invalidate_settings_cache(chat_id)
    await ax_mod.say(
        message,
        f"✅ <b>Delete flood {'on' if val else 'off'}</b>\n<blockquote>Flood messages are {'deleted' if val else 'kept'}</blockquote>",
        bot=bot,
    )


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(FloodRecordMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

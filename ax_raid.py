"""Antiraid: /antiraid /raidtime /raidactiontime /autoantiraid + join middleware (aiogram 3.x, bot_id aware)."""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

import ax_mod
import bot_context
from ax_common import cmd_args, fire, is_admin
from ax_warn2 import fmt_time, parse_time
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_raid")

_joins: dict[tuple[int, int], list[float]] = {}  # (bot_id, chat_id) -> list of join timestamps
_JOINS_MAX = 5000

_SC: dict[tuple[int, int], tuple[float, dict]] = {}
_SC_MAX = 5000
_SC_TTL = 20.0
_middleware_registered = False


def _prune_sc(now: float) -> None:
    if len(_SC) <= _SC_MAX:
        return
    expired = [k for k, (ts, _) in _SC.items() if now - ts >= _SC_TTL * 5]
    for k in expired:
        _SC.pop(k, None)
    if len(_SC) > _SC_MAX:
        oldest = sorted(_SC.items(), key=lambda kv: kv[1][0])[: _SC_MAX // 5]
        for k, _ in oldest:
            _SC.pop(k, None)


def _prune_joins(now: float) -> None:
    if len(_joins) <= _JOINS_MAX:
        return
    stale = [k for k, lst in _joins.items() if not lst or now - lst[-1] >= 60.0]
    for k in stale:
        _joins.pop(k, None)


async def _reload(pool: asyncpg.Pool, chat_id: int, bot_id: int) -> dict:
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT * FROM ax_raid_settings WHERE bot_id=$1 AND chat_id=$2",
            bot_id,
            chat_id,
        )
    v = (
        dict(r)
        if r
        else {"raid_until": None, "raid_secs": 21600, "action_secs": 3600, "auto_limit": 0}
    )
    now = time.time()
    _prune_sc(now)
    _SC[(bot_id, chat_id)] = (now, v)
    return dict(v)


async def get(pool: asyncpg.Pool, chat_id: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    key = (bid, chat_id)
    hit = _SC.get(key)
    if hit:
        now = time.time()
        if now - hit[0] >= _SC_TTL:
            _SC[key] = (now, hit[1])
            fire(_reload(pool, chat_id, bid))
        return dict(hit[1])
    return await _reload(pool, chat_id, bid)


async def save(pool: asyncpg.Pool, chat_id: int, col: str, val, bot_id: int) -> None:
    assert col in ("raid_until", "raid_secs", "action_secs", "auto_limit")
    _SC.pop((bot_id, chat_id), None)
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_raid_settings (bot_id, chat_id, {col}) VALUES ($1,$2,$3) "
            f"ON CONFLICT (chat_id) DO UPDATE SET bot_id=$1, {col}=$3",
            bot_id,
            chat_id,
            val,
        )


def active(s: dict) -> bool:
    ru = s.get("raid_until")
    if not ru:
        return False
    if ru.tzinfo is None:
        ru = ru.replace(tzinfo=timezone.utc)
    return ru > datetime.now(timezone.utc)


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


@router.message(Command("antiraid"))
async def antiraid_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    s = await get(pool, chat_id, bot_id=bid)
    args = cmd_args(message)
    if args and args[0].lower() in ("off", "no"):
        await save(pool, chat_id, "raid_until", None, bid)
        await ax_mod.say(
            message,
            "✅ <b>Antiraid off</b>\n<blockquote>New members can join normally again</blockquote>",
            bot=bot,
        )
        return
    if not args and active(s):
        ru = s["raid_until"]
        if ru.tzinfo is None:
            ru = ru.replace(tzinfo=timezone.utc)
        left = int((ru - datetime.now(timezone.utc)).total_seconds())
        await ax_mod.say(
            message,
            f"🚨 <b>Antiraid is ON</b>\n<blockquote>New joins are temp-banned for {fmt_time(s['action_secs'])}\n"
            f"⏳ Ends in <b>{fmt_time(max(60, left // 60 * 60))}</b>\nTurn off: /antiraid off</blockquote>",
            bot=bot,
        )
        return
    secs = parse_time(args[0]) if args else s["raid_secs"]
    if not secs:
        await ax_mod.say(
            message,
            "⏳ <b>Bad time</b>\n<blockquote>Example: /antiraid 3h (s, m, h, d, w) or /antiraid off</blockquote>",
            bot=bot,
        )
        return
    await save(pool, chat_id, "raid_until", datetime.now(timezone.utc) + timedelta(seconds=secs), bid)
    await ax_mod.say(
        message,
        f"🚨 <b>Antiraid ON</b>\n<blockquote>For <b>{fmt_time(secs)}</b>, every new member is banned for "
        f"<b>{fmt_time(s['action_secs'])}</b>\nTurn off: /antiraid off</blockquote>",
        bot=bot,
    )


async def _time_cmd(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    col: str,
    label: str,
    example: str,
    bid: int,
) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = cmd_args(message)
    if not args:
        s = await get(pool, chat_id, bot_id=bid)
        await ax_mod.say(
            message,
            f"⚙️ <b>{label}</b>\n<blockquote>Currently <b>{fmt_time(s[col])}</b></blockquote>",
            bot=bot,
        )
        return
    secs = parse_time(args[0])
    if not secs:
        await ax_mod.say(
            message,
            f"⏳ <b>Bad time</b>\n<blockquote>Example: {example}</blockquote>",
            bot=bot,
        )
        return
    await save(pool, chat_id, col, secs, bid)
    await ax_mod.say(
        message,
        f"✅ <b>{label} set</b>\n<blockquote><b>{fmt_time(secs)}</b></blockquote>",
        bot=bot,
    )


@router.message(Command("raidtime"))
async def raidtime_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await _time_cmd(message, bot, pool, "raid_secs", "Raid duration", "/raidtime 6h", bot_ctx.bot_id)


@router.message(Command("raidactiontime"))
async def raidactiontime_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await _time_cmd(message, bot, pool, "action_secs", "Raid ban length", "/raidactiontime 1h", bot_ctx.bot_id)


@router.message(Command("autoantiraid"))
async def autoantiraid_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    args = cmd_args(message)
    if not args:
        s = await get(pool, chat_id, bot_id=bid)
        cur = f"{s['auto_limit']} joins per minute" if s["auto_limit"] else "off"
        await ax_mod.say(
            message,
            f"⚙️ <b>Auto antiraid</b>\n<blockquote>Currently <b>{cur}</b>\n"
            "Example: /autoantiraid 15 or /autoantiraid off</blockquote>",
            bot=bot,
        )
        return
    if args[0].lower() in ("off", "no", "0"):
        await save(pool, chat_id, "auto_limit", 0, bid)
        await ax_mod.say(
            message,
            "✅ <b>Auto antiraid off</b>\n<blockquote>I won't switch it on by myself</blockquote>",
            bot=bot,
        )
        return
    if not args[0].isdigit() or not 2 <= int(args[0]) <= 500:
        await ax_mod.say(
            message,
            "💫 <b>Pick a number</b>\n<blockquote>Example: /autoantiraid 15 (2 to 500 joins per minute)</blockquote>",
            bot=bot,
        )
        return
    await save(pool, chat_id, "auto_limit", int(args[0]), bid)
    await ax_mod.say(
        message,
        f"✅ <b>Auto antiraid on</b>\n<blockquote>If <b>{args[0]}</b> people join within a minute, antiraid starts by itself 🚨</blockquote>",
        bot=bot,
    )


async def handle_join_raid(msg: Message, bot: Bot, pool: asyncpg.Pool) -> bool:
    """Returns True if raid mode blocked the join (stop welcome/captcha)."""
    chat = msg.chat
    if chat.type == "private" or not msg.new_chat_members:
        return False
    bid = bot.id
    jkey = (bid, chat.id)
    s = await get(pool, chat.id, bot_id=bid)
    now = time.time()
    if s["auto_limit"] > 0:
        _prune_joins(now)
        lst = [t for t in _joins.get(jkey, []) if now - t < 60.0] + [now] * len(msg.new_chat_members)
        _joins[jkey] = lst
        if len(lst) >= s["auto_limit"] and not active(s):
            until = datetime.now(timezone.utc) + timedelta(seconds=s["raid_secs"])
            await save(pool, chat.id, "raid_until", until, bid)
            s["raid_until"] = until
            try:
                await bot.send_message(
                    chat.id,
                    f"🚨 <b>Raid detected</b>\n<blockquote>{len(lst)} joins in a minute. "
                    f"Antiraid is ON for {fmt_time(s['raid_secs'])}.\nTurn off: /antiraid off</blockquote>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
    if not active(s):
        return False
    until = datetime.now(timezone.utc) + timedelta(seconds=s["action_secs"])
    for m in msg.new_chat_members:
        if m.id == bot.id:
            continue
        try:
            await bot.ban_chat_member(chat.id, m.id, until_date=until)
        except Exception as e:
            log.warning("raid ban failed: %s", e)
    try:
        await msg.delete()
    except Exception:
        pass
    return True


class RaidJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.new_chat_members:
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                if await handle_join_raid(event, bot, pool):
                    return None
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(RaidJoinMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

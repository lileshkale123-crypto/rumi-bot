"""Warn settings: /warnlimit /warnmode /warntime /resetwarn /resetallwarns /warnings (aiogram 3.x, bot_id aware)."""
import logging

import asyncpg
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

import ax_mod
import ax_warn
from ax_common import cmd_args, is_admin
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_warn2")

MODES = ("ban", "mute", "kick", "tban", "tmute")
DUR_WORDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_time(t: str | None) -> int | None:
    t = (t or "").lower().strip()
    if len(t) < 2 or t[-1] not in DUR_WORDS or not t[:-1].isdigit():
        return None
    return int(t[:-1]) * DUR_WORDS[t[-1]]


def fmt_time(secs: int) -> str:
    if secs <= 0:
        return "off"
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs % size == 0:
            return f"{secs // size}{unit}"
    return f"{secs}s"


async def admin_only(message: Message, bot: Bot) -> bool:
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


async def save(pool: asyncpg.Pool, chat_id: int, col: str, value, bot_id: int) -> None:
    assert col in ("warn_limit", "warn_mode", "warn_mode_secs", "warn_expire_secs")
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_warn_settings (bot_id, chat_id, {col}) VALUES ($1,$2,$3) "
            f"ON CONFLICT (chat_id) DO UPDATE SET bot_id=$1, {col}=$3",
            bot_id,
            chat_id,
            value,
        )
    ax_warn.invalidate_settings(chat_id, bot_id=bot_id)


@router.message(Command("warnlimit"))
async def warnlimit_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await admin_only(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    args = cmd_args(message)
    if not args:
        async with pool.acquire() as c:
            s = await ax_warn.get_settings(c, chat_id, bot_id=bid)
        await ax_mod.say(
            message,
            f"📊 <b>Warn limit</b>\n<blockquote>Currently <b>{s['warn_limit']}</b> warnings</blockquote>",
            bot=bot,
        )
        return
    if not args[0].isdigit() or not 1 <= int(args[0]) <= 100:
        await ax_mod.say(
            message,
            "💫 <b>Pick a number</b>\n<blockquote>Example: /warnlimit 5 (1 to 100)</blockquote>",
            bot=bot,
        )
        return
    await save(pool, chat_id, "warn_limit", int(args[0]), bid)
    await ax_mod.say(
        message,
        f"✅ <b>Warn limit set</b>\n<blockquote>Punishment after <b>{int(args[0])}</b> warnings</blockquote>",
        bot=bot,
    )


@router.message(Command("warnmode"))
async def warnmode_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await admin_only(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    args = cmd_args(message)
    if not args:
        async with pool.acquire() as c:
            s = await ax_warn.get_settings(c, chat_id, bot_id=bid)
        extra = f" for {fmt_time(s['warn_mode_secs'])}" if s["warn_mode"] in ("tban", "tmute") else ""
        await ax_mod.say(
            message,
            f"⚙️ <b>Warn mode</b>\n<blockquote>Currently <b>{s['warn_mode']}</b>{extra}</blockquote>",
            bot=bot,
        )
        return
    mode = args[0].lower()
    if mode not in MODES:
        await ax_mod.say(
            message,
            "💫 <b>Pick a mode</b>\n<blockquote>ban / mute / kick / tban / tmute\nExample: /warnmode tmute 6h</blockquote>",
            bot=bot,
        )
        return
    secs = 86400
    if mode in ("tban", "tmute"):
        if len(args) > 1:
            secs = parse_time(args[1])
            if not secs:
                await ax_mod.say(
                    message,
                    "⏳ <b>Bad time</b>\n<blockquote>Example: /warnmode tmute 6h (s, m, h, d, w)</blockquote>",
                    bot=bot,
                )
                return
        await save(pool, chat_id, "warn_mode_secs", secs, bid)
    await save(pool, chat_id, "warn_mode", mode, bid)
    extra = f" for <b>{fmt_time(secs)}</b>" if mode in ("tban", "tmute") else ""
    await ax_mod.say(
        message,
        f"✅ <b>Warn mode set</b>\n<blockquote>Limit reached → <b>{mode}</b>{extra}</blockquote>",
        bot=bot,
    )


@router.message(Command("warntime"))
async def warntime_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await admin_only(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    args = cmd_args(message)
    if not args:
        async with pool.acquire() as c:
            s = await ax_warn.get_settings(c, chat_id, bot_id=bid)
        await ax_mod.say(
            message,
            f"⏳ <b>Warn expiry</b>\n<blockquote>Warnings last: <b>{fmt_time(s['warn_expire_secs'])}</b></blockquote>",
            bot=bot,
        )
        return
    if args[0].lower() in ("off", "no", "0"):
        await save(pool, chat_id, "warn_expire_secs", 0, bid)
        await ax_mod.say(
            message,
            "✅ <b>Expiry off</b>\n<blockquote>Warnings now last forever</blockquote>",
            bot=bot,
        )
        return
    secs = parse_time(args[0])
    if not secs:
        await ax_mod.say(
            message,
            "⏳ <b>Bad time</b>\n<blockquote>Example: /warntime 4w (s, m, h, d, w) or /warntime off</blockquote>",
            bot=bot,
        )
        return
    await save(pool, chat_id, "warn_expire_secs", secs, bid)
    await ax_mod.say(
        message,
        f"✅ <b>Expiry set</b>\n<blockquote>Warnings vanish after <b>{fmt_time(secs)}</b></blockquote>",
        bot=bot,
    )


@router.message(Command("resetwarn"))
async def resetwarn_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    target = await ax_mod.gate(message, bot, pool, False)
    if target is None:
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_warn_log WHERE bot_id=$1 AND chat_id=$2 AND user_id=$3",
            bid,
            chat_id,
            target.id,
        )
    await ax_mod.say(
        message,
        ax_mod.card("✅", "Warnings reset", target.first_name or "User", "\n📊 Back to 0"),
        bot=bot,
    )


@router.message(Command("resetallwarns"))
async def resetallwarns_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await admin_only(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_warn_log WHERE bot_id=$1 AND chat_id=$2", bid, chat_id)
    await ax_mod.say(
        message,
        "✅ <b>All warnings cleared</b>\n<blockquote>Everyone is back to 0 💜</blockquote>",
        bot=bot,
    )


@router.message(Command("warnings"))
async def warnings_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await admin_only(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        s = await ax_warn.get_settings(c, chat_id, bot_id=bid)
    extra = f" ({fmt_time(s['warn_mode_secs'])})" if s["warn_mode"] in ("tban", "tmute") else ""
    await ax_mod.say(
        message,
        (
            "⚙️ <b>Warn settings</b>\n<blockquote>"
            f"📊 <b>Limit:</b> {s['warn_limit']}\n"
            f"🔨 <b>Action:</b> {s['warn_mode']}{extra}\n"
            f"⏳ <b>Expiry:</b> {fmt_time(s['warn_expire_secs'])}"
            "</blockquote>"
        ),
        bot=bot,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

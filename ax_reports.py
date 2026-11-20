"""Reports: /report, @admin (as a reply) and /reports on|off (aiogram 3.x)."""
import time

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, ReplyParameters

import ax_mod
from ax_common import cmd_args, esc, is_admin

router = Router(name="ax_reports")

_cache: dict[int, tuple[float, bool]] = {}
_CACHE_TTL = 60.0
_CACHE_MAX = 5000


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX:
        return
    expired = [cid for cid, (ts, _) in _cache.items() if now - ts >= _CACHE_TTL]
    for cid in expired:
        _cache.pop(cid, None)
    if len(_cache) > _CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for cid, _ in oldest:
            _cache.pop(cid, None)


def invalidate(chat_id: int) -> None:
    _cache.pop(chat_id, None)


async def enabled(pool: asyncpg.Pool, chat_id: int) -> bool:
    now = time.time()
    hit = _cache.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    async with pool.acquire() as c:
        v = await c.fetchval("SELECT enabled FROM ax_report_settings WHERE chat_id=$1", chat_id)
    val = True if v is None else bool(v)
    _prune_cache(now)
    _cache[chat_id] = (now, val)
    return val


async def _do_report(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type == "private" or not message.reply_to_message:
        if chat.type != "private":
            await ax_mod.say(
                message,
                "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want to report</blockquote>",
                bot=bot,
            )
        return
    if not await enabled(pool, chat.id):
        return
    target = message.reply_to_message.from_user
    if target is None or target.is_bot:
        return
    if await is_admin(bot, chat.id, user.id):
        await ax_mod.say(
            message,
            "🛡️ <b>You are an admin</b>\n<blockquote>Admins can act directly, no report needed ✨</blockquote>",
            bot=bot,
        )
        return
    if await is_admin(bot, chat.id, target.id):
        await ax_mod.say(
            message,
            "🛡️ <b>Can't report an admin</b>\n<blockquote>Admins are trusted here ✨</blockquote>",
            bot=bot,
        )
        return

    try:
        admins = await bot.get_chat_administrators(chat.id)
    except Exception:
        admins = []
    tags = "".join(
        f'<a href="tg://user?id={a.user.id}">\u200b</a>'
        for a in admins
        if not a.user.is_bot
    )
    await bot.send_message(
        chat.id,
        f"🚨 <b>Reported</b>\n<blockquote>👤 <b>{esc(target.first_name or 'Member')}</b> was reported to the admins by "
        f"<b>{esc(user.first_name or 'Member')}</b>. They will take a look 💜</blockquote>{tags}",
        parse_mode=ParseMode.HTML,
        reply_parameters=ReplyParameters(
            message_id=message.reply_to_message.message_id,
            allow_sending_without_reply=True,
        ),
    )


@router.message(Command("report"))
async def report_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    await _do_report(message, bot, pool)


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.reply_to_message,
    F.text.regexp(r"(?i)^\s*@admins?\b"),
)
async def atadmin_watch(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    await _do_report(message, bot, pool)


@router.message(Command("reports"))
async def reports_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type == "private" or not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>",
            bot=bot,
        )
        return
    args = cmd_args(message)
    arg = args[0].lower() if args else ""
    if arg not in ("on", "off", "yes", "no"):
        cur = await enabled(pool, chat.id)
        await ax_mod.say(
            message,
            f"⚙️ <b>Reports</b>\n<blockquote>Currently <b>{'on' if cur else 'off'}</b>\nUse /reports on or off</blockquote>",
            bot=bot,
        )
        return
    val = arg in ("on", "yes")
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_report_settings (chat_id, enabled) VALUES ($1,$2) "
            "ON CONFLICT (chat_id) DO UPDATE SET enabled=$2",
            chat.id,
            val,
        )
    invalidate(chat.id)
    await ax_mod.say(
        message,
        f"✅ <b>Reports {'on' if val else 'off'}</b>\n<blockquote>Members {'can' if val else 'can no longer'} report messages</blockquote>",
        bot=bot,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

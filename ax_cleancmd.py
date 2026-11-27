"""Clean command messages after they run (aiogram 3.x + APScheduler)."""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ax_common import cmd_args, fire, get_bot_username, is_admin

router = Router(name="ax_cleancmd")
H = ParseMode.HTML
TYPES = ("admin", "user", "other")
_cache: dict[int, set[str]] = {}
_CACHE_MAX = 5000
_middleware_registered = False


async def kinds(pool: asyncpg.Pool, chat_id: int) -> set[str]:
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT kind FROM ax_cleancmd WHERE chat_id=$1", chat_id)
    k = {r["kind"] for r in rows}
    if len(_cache) >= _CACHE_MAX and chat_id not in _cache:
        _cache.pop(next(iter(_cache)), None)
    _cache[chat_id] = k
    return k


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


def _parse(args: list[str]) -> set[str]:
    out: set[str] = set()
    for a in args:
        a = a.lower()
        if a == "all":
            out |= set(TYPES)
        elif a in TYPES:
            out.add(a)
    return out


@router.message(Command("cleancommand"))
async def cleancommand(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    want = _parse(cmd_args(message))
    if not want:
        cur = await kinds(pool, cid)
        await message.reply(
            "🧹 <b>Clean commands</b>\n<blockquote>On for: <b>"
            f"{', '.join(sorted(cur)) or 'nothing'}</b>\n"
            "Use /cleancommand all, admin, user or other</blockquote>",
            parse_mode=H,
        )
        return
    async with pool.acquire() as c:
        for k in want:
            await c.execute(
                "INSERT INTO ax_cleancmd VALUES ($1,$2) ON CONFLICT DO NOTHING",
                cid,
                k,
            )
    await kinds(pool, cid)
    await message.reply(
        f"🧹 <b>Cleaning</b> <b>{', '.join(sorted(want))}</b> commands ✨",
        parse_mode=H,
    )


@router.message(Command("keepcommand"))
async def keepcommand(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    want = _parse(cmd_args(message))
    if not want:
        await message.reply(
            "<blockquote>Use /keepcommand all, admin, user or other</blockquote>",
            parse_mode=H,
        )
        return
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_cleancmd WHERE chat_id=$1 AND kind=ANY($2::text[])",
            cid,
            list(want),
        )
    await kinds(pool, cid)
    await message.reply(
        f"✅ <b>Keeping</b> <b>{', '.join(sorted(want))}</b> commands",
        parse_mode=H,
    )


@router.message(Command("cleancommandtypes"))
async def types(message: Message) -> None:
    await message.reply(
        "🧹 <b>Command types</b>\n<blockquote>admin: sent by admins\n"
        "user: sent by members\nother: aimed at other bots\n"
        "all: every type</blockquote>",
        parse_mode=H,
    )


async def _del(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


class CleanCmdMiddleware(BaseMiddleware):
    """Schedules deletion of group commands matching ax_cleancmd without blocking command execution."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type in ("group", "supergroup")
            and event.from_user is not None
            and event.text
            and event.text.startswith("/")
        ):
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            scheduler: AsyncIOScheduler | None = data.get("scheduler")
            if bot is not None and pool is not None and scheduler is not None:
                # decided in the background: the command itself never waits for this
                fire(_plan_clean(event, bot, pool, scheduler, data.get("bot_username")))
        return await handler(event, data)


async def _plan_clean(event: Message, bot: Bot, pool: asyncpg.Pool, scheduler: AsyncIOScheduler, bot_username) -> None:
    k = _cache.get(event.chat.id)
    if k is None:
        k = await kinds(pool, event.chat.id)
    if not k:
        return
    first = event.text.split()[0]
    uname = (bot_username or await get_bot_username(bot)).lower()
    me = "@" + uname if uname else ""
    if "@" in first and (not me or not first.lower().endswith(me)):
        kind = "other"
    elif await is_admin(bot, event.chat.id, event.from_user.id):
        kind = "admin"
    else:
        kind = "user"
    if kind in k:
        run_at = datetime.now(timezone.utc) + timedelta(seconds=3)
        scheduler.add_job(
            _del,
            trigger="date",
            run_date=run_at,
            args=[bot, event.chat.id, event.message_id],
            id=f"cleancmd:{event.chat.id}:{event.message_id}",
            replace_existing=True,
        )


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(CleanCmdMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

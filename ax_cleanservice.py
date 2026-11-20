"""Delete Telegram service messages (joins, leaves, pins, etc) (aiogram 3.x + APScheduler)."""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ax_common import cmd_args, is_admin

router = Router(name="ax_cleanservice")
H = ParseMode.HTML
TYPES = ("join", "leave", "other", "photo", "pin", "title", "videochat")
_cache: dict[int, set[str]] = {}
_CACHE_MAX = 5000
_middleware_registered = False


async def kinds(pool: asyncpg.Pool, chat_id: int) -> set[str]:
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT kind FROM ax_cleanservice WHERE chat_id=$1", chat_id)
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


@router.message(Command("cleanservice"))
async def cleanservice(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    want = _parse(cmd_args(message))
    if not want:
        cur = await kinds(pool, cid)
        await message.reply(
            "🧹 <b>Clean service</b>\n<blockquote>On for: <b>"
            f"{', '.join(sorted(cur)) or 'nothing'}</b>\n"
            "Use /cleanservice all, join, leave, other, photo, pin, "
            "title or videochat</blockquote>",
            parse_mode=H,
        )
        return
    async with pool.acquire() as c:
        for k in want:
            await c.execute(
                "INSERT INTO ax_cleanservice VALUES ($1,$2) ON CONFLICT DO NOTHING",
                cid,
                k,
            )
    await kinds(pool, cid)
    await message.reply(
        f"🧹 <b>Cleaning</b> <b>{', '.join(sorted(want))}</b> ✨",
        parse_mode=H,
    )


@router.message(Command("keepservice", "nocleanservice"))
async def keepservice(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    want = _parse(cmd_args(message))
    if not want:
        await message.reply(
            "<blockquote>Use /keepservice all, join, leave, other, "
            "photo, pin, title or videochat</blockquote>",
            parse_mode=H,
        )
        return
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_cleanservice WHERE chat_id=$1 AND kind=ANY($2::text[])",
            cid,
            list(want),
        )
    await kinds(pool, cid)
    await message.reply(
        f"✅ <b>Keeping</b> <b>{', '.join(sorted(want))}</b>",
        parse_mode=H,
    )


@router.message(Command("cleanservicetypes"))
async def types(message: Message) -> None:
    await message.reply(
        "🧹 <b>Service types</b>\n<blockquote>join: new members\n"
        "leave: members leaving\nphoto: group photo changes\n"
        "pin: pinned message notices\ntitle: name changes\n"
        "videochat: video chat notices\nother: everything else\n"
        "all: every type</blockquote>",
        parse_mode=H,
    )


def kind_of(m: Message) -> str | None:
    if m.new_chat_members:
        return "join"
    if m.left_chat_member:
        return "leave"
    if m.new_chat_photo or m.delete_chat_photo:
        return "photo"
    if m.pinned_message:
        return "pin"
    if m.new_chat_title:
        return "title"
    if (
        m.video_chat_started
        or m.video_chat_ended
        or m.video_chat_scheduled
        or m.video_chat_participants_invited
    ):
        return "videochat"
    if (
        m.group_chat_created
        or m.supergroup_chat_created
        or m.channel_chat_created
        or m.message_auto_delete_timer_changed
        or m.migrate_to_chat_id
        or m.migrate_from_chat_id
    ):
        return "other"
    return None


async def _del(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


class CleanServiceMiddleware(BaseMiddleware):
    """Schedules deletion of group service messages without blocking welcome/captcha/raid."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.type in ("group", "supergroup"):
            kind = kind_of(event)
            if kind:
                bot: Bot | None = data.get("bot")
                pool: asyncpg.Pool | None = data.get("pool")
                scheduler: AsyncIOScheduler | None = data.get("scheduler")
                if bot is not None and pool is not None and scheduler is not None:
                    k = _cache.get(event.chat.id)
                    if k is None:
                        k = await kinds(pool, event.chat.id)
                    if kind in k:
                        run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
                        scheduler.add_job(
                            _del,
                            trigger="date",
                            run_date=run_at,
                            args=[bot, event.chat.id, event.message_id],
                            id=f"cleansvc:{event.chat.id}:{event.message_id}",
                            replace_existing=True,
                        )
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(CleanServiceMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

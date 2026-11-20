"""Remembers users and groups per bot so @username targeting works (aiogram 3.x + APScheduler)."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated, Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import bot_context
from ax_common import fire, track_user

log = logging.getLogger(__name__)

# All caches keyed by bot_id so clones never leak tracked users or groups
_seen_groups: set[tuple[int, int]] = set()                             # (bot_id, chat_id)
_known: dict[tuple[int, int, int], tuple[str | None, str | None]] = {} # (bot_id, user_id, gid)
_KNOWN_MAX = 50_000
_counts: dict[tuple[int, int], int] = {}                               # (bot_id, user_id) -> delta
_middleware_registered = False


async def _track_cached(pool: asyncpg.Pool, user, chat, bot_id: int) -> None:
    if user is None or user.is_bot:
        return
    gid = 0
    if chat is not None and chat.type != "private":
        gid = chat.id
    key = (bot_id, user.id, gid)
    sig = (user.username, user.first_name)
    if _known.get(key) == sig:
        ckey = (bot_id, user.id)
        _counts[ckey] = _counts.get(ckey, 0) + 1
        return
    await track_user(pool, user, chat, bot_id=bot_id)
    if len(_known) >= _KNOWN_MAX:
        _known.clear()
    _known[key] = sig


async def _flush_counts(pool: asyncpg.Pool) -> None:
    if not _counts:
        return
    items = list(_counts.items())
    _counts.clear()
    rows = [(bid, uid, n) for (bid, uid), n in items]
    try:
        async with pool.acquire() as c:
            await c.executemany(
                "UPDATE ax_user_stats SET message_count = message_count + $3, "
                "last_seen = now() WHERE bot_id = $1 AND user_id = $2",
                rows,
            )
    except Exception as e:
        log.warning("flush counts error: %s", e)
        for (bid, uid), n in items:
            _counts[(bid, uid)] = _counts.get((bid, uid), 0) + n


async def _tracker_work(pool: asyncpg.Pool, user, chat, bot_id: int) -> None:
    try:
        await _track_cached(pool, user, chat, bot_id)
        gkey = (bot_id, chat.id) if chat is not None else None
        if (
            chat is not None
            and chat.type in ("group", "supergroup")
            and gkey not in _seen_groups
        ):
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ax_groups (bot_id, chat_id, title) VALUES ($1, $2, $3) "
                    "ON CONFLICT (chat_id) DO UPDATE SET bot_id = EXCLUDED.bot_id, title = EXCLUDED.title",
                    bot_id,
                    chat.id,
                    chat.title or "Group",
                )
            _seen_groups.add(gkey)
    except Exception as e:
        log.warning("tracker error: %s", e)


async def _my_chat_member_work(pool: asyncpg.Pool, r: ChatMemberUpdated, bot_id: int) -> None:
    if r.chat.type == "private":
        return
    gkey = (bot_id, r.chat.id)
    try:
        async with pool.acquire() as c:
            if r.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                await c.execute(
                    "DELETE FROM ax_groups WHERE bot_id = $1 AND chat_id = $2",
                    bot_id,
                    r.chat.id,
                )
                _seen_groups.discard(gkey)
            elif r.chat.type in ("group", "supergroup"):
                await c.execute(
                    "INSERT INTO ax_groups (bot_id, chat_id, title) VALUES ($1, $2, $3) "
                    "ON CONFLICT (chat_id) DO UPDATE SET bot_id = EXCLUDED.bot_id, title = EXCLUDED.title",
                    bot_id,
                    r.chat.id,
                    r.chat.title or "Group",
                )
                _seen_groups.add(gkey)
    except Exception as e:
        log.warning("my_chat_member track error: %s", e)


class TrackMessageMiddleware(BaseMiddleware):
    """Non-blocking middleware to track users and groups on every message."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            pool: asyncpg.Pool | None = data.get("pool")
            if pool is not None:
                bot = data.get("bot")
                bid = data.get("bot_id") or bot_context.resolve_bot_id(getattr(bot, "id", None))
                fire(_tracker_work(pool, event.from_user, event.chat, bid))
        return await handler(event, data)


class TrackMyChatMemberMiddleware(BaseMiddleware):
    """Non-blocking observer on my_chat_member to maintain ax_groups."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, ChatMemberUpdated):
            pool: asyncpg.Pool | None = data.get("pool")
            if pool is not None:
                bot = data.get("bot")
                bid = data.get("bot_id") or bot_context.resolve_bot_id(getattr(bot, "id", None))
                fire(_my_chat_member_work(pool, event, bid))
        return await handler(event, data)


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    pool: asyncpg.Pool = dispatcher["pool"]
    scheduler.add_job(
        _flush_counts,
        trigger="interval",
        seconds=300,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=300),
        args=[pool],
        id="ax_track:flush_counts",
        replace_existing=True,
    )


async def on_shutdown(bot: Bot, dispatcher) -> None:
    pool: asyncpg.Pool | None = dispatcher.get("pool")
    if pool is not None:
        await _flush_counts(pool)


def register(dp) -> None:
    global _middleware_registered
    if _middleware_registered:
        return
    _middleware_registered = True
    dp.message.outer_middleware(TrackMessageMiddleware())
    dp.my_chat_member.outer_middleware(TrackMyChatMemberMiddleware())

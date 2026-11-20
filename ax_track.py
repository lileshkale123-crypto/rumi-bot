"""Remembers users and groups so @username targeting works (aiogram 3.x + APScheduler)."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated, Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ax_common import fire, track_user

log = logging.getLogger(__name__)

_seen_groups: set[int] = set()
_known: dict[tuple[int, int], tuple[str | None, str | None]] = {}
_KNOWN_MAX = 50_000
_counts: dict[int, int] = {}
_middleware_registered = False


async def _track_cached(pool: asyncpg.Pool, user, chat) -> None:
    if user is None or user.is_bot:
        return
    gid = 0
    if chat is not None and chat.type != "private":
        gid = chat.id
    key = (user.id, gid)
    sig = (user.username, user.first_name)
    if _known.get(key) == sig:
        _counts[user.id] = _counts.get(user.id, 0) + 1
        return
    await track_user(pool, user, chat)
    if len(_known) >= _KNOWN_MAX:
        _known.clear()
    _known[key] = sig


async def _flush_counts(pool: asyncpg.Pool) -> None:
    if not _counts:
        return
    items = list(_counts.items())
    _counts.clear()
    try:
        async with pool.acquire() as c:
            await c.executemany(
                "UPDATE ax_user_stats SET message_count = message_count + $2, "
                "last_seen = now() WHERE user_id = $1",
                items,
            )
    except Exception as e:
        log.warning("flush counts error: %s", e)
        for uid, n in items:
            _counts[uid] = _counts.get(uid, 0) + n


async def _tracker_work(pool: asyncpg.Pool, user, chat) -> None:
    try:
        await _track_cached(pool, user, chat)
        if chat is not None and chat.type in ("group", "supergroup") and chat.id not in _seen_groups:
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ax_groups (chat_id, title) VALUES ($1, $2) "
                    "ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title",
                    chat.id,
                    chat.title or "Group",
                )
            _seen_groups.add(chat.id)
    except Exception as e:
        log.warning("tracker error: %s", e)


async def _my_chat_member_work(pool: asyncpg.Pool, r: ChatMemberUpdated) -> None:
    if r.chat.type == "private":
        return
    try:
        async with pool.acquire() as c:
            if r.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                await c.execute("DELETE FROM ax_groups WHERE chat_id=$1", r.chat.id)
                _seen_groups.discard(r.chat.id)
            elif r.chat.type in ("group", "supergroup"):
                await c.execute(
                    "INSERT INTO ax_groups (chat_id, title) VALUES ($1, $2) "
                    "ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title",
                    r.chat.id,
                    r.chat.title or "Group",
                )
                _seen_groups.add(r.chat.id)
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
                fire(_tracker_work(pool, event.from_user, event.chat))
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
                fire(_my_chat_member_work(pool, event))
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

"""Channel protection: short branded post (auto-deleted after 3.5h) and private member-drop alerts (aiogram 3.x + APScheduler)."""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    TelegramObject,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ax_common import fire, get_bot_username

log = logging.getLogger(__name__)
H = ParseMode.HTML
DELETE_AFTER = timedelta(hours=3, minutes=30)
CLEAN_EVERY = 300
CHECK_EVERY = 6 * 3600
RIGHTS = "post_messages+edit_messages+delete_messages+invite_users"
_middleware_registered = False


def _kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Add to your channel",
                    url=f"https://t.me/{bot_username}?startchannel=true&admin={RIGHTS}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Add to your group",
                    url=f"https://t.me/{bot_username}?startgroup=true",
                )
            ],
        ]
    )


async def on_my_member(cm: ChatMemberUpdated, bot: Bot, pool: asyncpg.Pool) -> None:
    if cm.chat.type != "channel":
        return
    chat = cm.chat
    status = cm.new_chat_member.status
    if status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        await pool.execute("DELETE FROM ax_channels WHERE chat_id=$1", chat.id)
        return
    if (
        status != ChatMemberStatus.ADMINISTRATOR
        or cm.old_chat_member.status == ChatMemberStatus.ADMINISTRATOR
    ):
        return

    try:
        count = await bot.get_chat_member_count(chat.id)
    except Exception:
        count = None

    added_by = cm.from_user.id if cm.from_user else None
    await pool.execute(
        "INSERT INTO ax_channels (chat_id, title, added_by, last_count) VALUES ($1,$2,$3,$4) "
        "ON CONFLICT (chat_id) DO UPDATE SET title=$2, added_by=$3, last_count=$4",
        chat.id,
        chat.title,
        added_by,
        count,
    )

    text = (
        "🛡 <b>Protected by Rumi</b>\n"
        f"<blockquote>{html.escape(chat.title or 'This channel')} is now watched 💜\n"
        "Want the same for yours? Tap below ✨</blockquote>"
    )
    uname = await get_bot_username(bot)
    try:
        sent = await bot.send_message(
            chat.id,
            text,
            parse_mode=H,
            reply_markup=_kb(uname),
        )
    except Exception as e:
        log.warning("channel post failed: %s", e)
        return

    await pool.execute(
        "INSERT INTO ax_chan_cleanup (chat_id, message_id, delete_at) VALUES ($1,$2,$3) "
        "ON CONFLICT DO NOTHING",
        chat.id,
        sent.message_id,
        datetime.now(timezone.utc) + DELETE_AFTER,
    )


class ChannelMemberMiddleware(BaseMiddleware):
    """Non-blocking observer on my_chat_member for channels."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, ChatMemberUpdated) and event.chat.type == "channel":
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                fire(on_my_member(event, bot, pool))
        return await handler(event, data)


async def cleanup_job(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        rows = await pool.fetch(
            "SELECT chat_id, message_id FROM ax_chan_cleanup WHERE delete_at <= now() LIMIT 50"
        )
        for r in rows:
            try:
                await bot.delete_message(r["chat_id"], r["message_id"])
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
                try:
                    await bot.delete_message(r["chat_id"], r["message_id"])
                except Exception:
                    pass
            except Exception as e:
                log.info("channel cleanup skip: %s", e)
            await pool.execute(
                "DELETE FROM ax_chan_cleanup WHERE chat_id=$1 AND message_id=$2",
                r["chat_id"],
                r["message_id"],
            )
    except Exception:
        log.exception("channel cleanup_job failed")


async def count_job(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        rows = await pool.fetch("SELECT chat_id, title, added_by, last_count FROM ax_channels")
        for r in rows:
            try:
                now = await bot.get_chat_member_count(r["chat_id"])
            except Exception:
                continue
            old = r["last_count"]
            await pool.execute(
                "UPDATE ax_channels SET last_count=$1 WHERE chat_id=$2",
                now,
                r["chat_id"],
            )
            if old is None or r["added_by"] is None:
                continue
            drop = old - now
            if drop >= max(10, old // 10):
                try:
                    await bot.send_message(
                        r["added_by"],
                        f"📉 <b>Member drop</b>\n<blockquote>{html.escape(r['title'] or 'Your channel')} "
                        f"went from <code>{old:,}</code> to <code>{now:,}</code> members "
                        f"(<code>-{drop:,}</code>) since my last check.</blockquote>",
                        parse_mode=H,
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.05)
    except Exception:
        log.exception("channel count_job failed")


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    pool: asyncpg.Pool = dispatcher["pool"]
    now = datetime.now(timezone.utc)
    scheduler.add_job(
        cleanup_job,
        trigger="interval",
        seconds=CLEAN_EVERY,
        next_run_time=now + timedelta(seconds=60),
        args=[bot, pool],
        id="ax_channel:cleanup_job",
        replace_existing=True,
    )
    scheduler.add_job(
        count_job,
        trigger="interval",
        seconds=CHECK_EVERY,
        next_run_time=now + timedelta(seconds=CHECK_EVERY),
        args=[bot, pool],
        id="ax_channel:count_job",
        replace_existing=True,
    )


def register(dp) -> None:
    global _middleware_registered
    if _middleware_registered:
        return
    _middleware_registered = True
    dp.my_chat_member.outer_middleware(ChannelMemberMiddleware())

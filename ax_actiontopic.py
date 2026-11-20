"""Action topic: send moderation messages into one forum topic (aiogram 3.x + APScheduler)."""
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ax_common import cmd_args, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_actiontopic")
H = ParseMode.HTML
MAP: dict[int, int] = {}


async def _say(message: Message, text: str):
    return await message.reply(text, parse_mode=H)


async def _load(pool: asyncpg.Pool) -> None:
    try:
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT chat_id, thread_id FROM ax_action_topics")
        MAP.clear()
        for r in rows:
            MAP[r["chat_id"]] = r["thread_id"]
        log.info("action topics loaded: %d", len(rows))
    except Exception as e:
        log.warning("action topic load failed: %s", e)


async def _check(message: Message, bot: Bot) -> bool:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False
    if chat.type != "supergroup" or not getattr(chat, "is_forum", False):
        await _say(
            message,
            "<b>Topics only</b>\n<blockquote>This works in groups that have Topics turned on \u2728</blockquote>",
        )
        return False
    if not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await _say(
            message,
            "\U0001F512 <b>Admins only</b>\n<blockquote>Only admins can choose the action topic \u2728</blockquote>",
        )
        return False
    return True


@router.message(Command("setactiontopic"))
async def set_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _check(message, bot):
        return
    chat = message.chat
    tid = message.message_thread_id if message.is_topic_message else None
    if not tid:
        await _say(
            message,
            "\U0001F4CC <b>Use it inside a topic</b>\n<blockquote>Send /setactiontopic in the topic where you want moderation messages \U0001F4AB</blockquote>",
        )
        return
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_action_topics (chat_id, thread_id) VALUES ($1, $2) "
            "ON CONFLICT (chat_id) DO UPDATE SET thread_id = EXCLUDED.thread_id",
            chat.id,
            tid,
        )
    MAP[chat.id] = tid
    await _say(
        message,
        "\u2705 <b>Action topic set</b>\n<blockquote>Bans, warns, mutes and other moderation messages will now appear in this topic \u2728</blockquote>",
    )


@router.message(Command("actiontopic"))
async def show_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _check(message, bot):
        return
    chat = message.chat
    args = cmd_args(message)
    if args and args[0].lower() == "off":
        async with pool.acquire() as c:
            await c.execute("DELETE FROM ax_action_topics WHERE chat_id = $1", chat.id)
        MAP.pop(chat.id, None)
        await _say(
            message,
            "\U0001F9F9 <b>Action topic cleared</b>\n<blockquote>Moderation messages will appear where the command is used \u2728</blockquote>",
        )
        return
    tid = MAP.get(chat.id)
    if tid:
        await _say(
            message,
            "\U0001F4CC <b>Action topic is ON</b>\n<blockquote>Moderation messages go to the topic you chose.\nTurn it off with /actiontopic off \u2728</blockquote>",
        )
    else:
        await _say(
            message,
            "\U0001F4CC <b>Action topic is OFF</b>\n<blockquote>Open a topic and send /setactiontopic there to choose it \u2728</blockquote>",
        )


async def on_startup(bot: Bot, dispatcher) -> None:
    pool: asyncpg.Pool = dispatcher["pool"]
    scheduler: AsyncIOScheduler | None = dispatcher.get("scheduler")
    await _load(pool)
    if scheduler is not None:
        scheduler.add_job(
            _load,
            trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=5),
            args=[pool],
            id="ax_actiontopic:load",
            replace_existing=True,
        )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

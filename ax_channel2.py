"""Channel promo post every 2 days and weekly private summary (aiogram 3.x + APScheduler)."""
import asyncio
import html
import logging
import random
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ax_channel import CHECK_EVERY, DELETE_AFTER, H, _kb
from ax_common import get_bot_username

log = logging.getLogger(__name__)


def _plural(n: int) -> str:
    return "1 channel" if n == 1 else f"{n:,} channels"


async def promo_job(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        rows = await pool.fetch(
            "SELECT chat_id, title FROM ax_channels "
            "WHERE last_promo <= now() - interval '2 days' LIMIT 20"
        )
        if not rows:
            return
        tot = await pool.fetch("SELECT count(*) AS n FROM ax_channels")
        total = tot[0]["n"]
        uname = await get_bot_username(bot)

        for r in rows:
            await pool.execute(
                "UPDATE ax_channels SET last_promo=now() WHERE chat_id=$1",
                r["chat_id"],
            )
            try:
                count = await bot.get_chat_member_count(r["chat_id"])
            except Exception:
                count = None
            line = f"<code>{count:,}</code> members" if count else "its members"
            name = html.escape(r["title"] or "This channel")
            hd = random.choice([
                "ᴘʀᴏᴛᴇᴄᴛᴇᴅ ʙʏ ʀᴜᴍɪ",
                "ᴛʜɪs ᴄʜᴀɴɴᴇʟ ɪs sᴀғᴇ",
                "ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ ɪs ᴡᴀᴛᴄʜᴇᴅ",
            ])
            text = (
                f"\U0001F6E1 <b>{hd}</b>\n"
                f"<blockquote>{name} has {line} being watched \U0001F49C\n"
                f"Rumi guards {_plural(total)} right now.\n"
                "ᴡᴀɴᴛ ᴛʜᴇ sᴀᴍᴇ ғᴏʀ ʏᴏᴜʀs? ᴛᴀᴘ ʙᴇʟᴏᴡ \u2728</blockquote>"
            )
            sent = None
            try:
                sent = await bot.send_message(
                    r["chat_id"],
                    text,
                    parse_mode=H,
                    reply_markup=_kb(uname),
                )
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
                try:
                    sent = await bot.send_message(
                        r["chat_id"],
                        text,
                        parse_mode=H,
                        reply_markup=_kb(uname),
                    )
                except Exception as e:
                    log.info("promo post skipped: %s", e)
                    continue
            except Exception as e:
                log.info("promo post skipped: %s", e)
                continue

            if sent is not None:
                await pool.execute(
                    "INSERT INTO ax_chan_cleanup (chat_id, message_id, delete_at) "
                    "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                    r["chat_id"],
                    sent.message_id,
                    datetime.now(timezone.utc) + DELETE_AFTER,
                )
            await asyncio.sleep(0.1)
    except Exception:
        log.exception("channel promo_job failed")


async def week_job(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        rows = await pool.fetch(
            "SELECT chat_id, title, added_by, last_count, week_count "
            "FROM ax_channels WHERE week_at <= now() - interval '7 days' LIMIT 20"
        )
        for r in rows:
            try:
                now = await bot.get_chat_member_count(r["chat_id"])
            except Exception:
                continue
            base = r["week_count"] if r["week_count"] is not None else r["last_count"]
            await pool.execute(
                "UPDATE ax_channels SET week_count=$1, week_at=now() WHERE chat_id=$2",
                now,
                r["chat_id"],
            )
            if base is None or r["added_by"] is None:
                continue
            name = html.escape(r["title"] or "Your channel")
            try:
                await bot.send_message(
                    r["added_by"],
                    "\U0001F4C8 <b>Weekly summary</b>\n"
                    f"<blockquote>{name}\n"
                    f"Members now: <code>{now:,}</code>\n"
                    f"Change this week: <code>{now - base:+,}</code></blockquote>",
                    parse_mode=H,
                )
            except Exception:
                pass
            await asyncio.sleep(0.05)
    except Exception:
        log.exception("channel week_job failed")


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    pool: asyncpg.Pool = dispatcher["pool"]
    now = datetime.now(timezone.utc)
    scheduler.add_job(
        promo_job,
        trigger="interval",
        seconds=3600,
        next_run_time=now + timedelta(seconds=180),
        args=[bot, pool],
        id="ax_channel2:promo_job",
        replace_existing=True,
    )
    scheduler.add_job(
        week_job,
        trigger="interval",
        seconds=CHECK_EVERY,
        next_run_time=now + timedelta(seconds=1200),
        args=[bot, pool],
        id="ax_channel2:week_job",
        replace_existing=True,
    )


def register(dp) -> None:
    pass

"""Weekly leaderboard: auto-posts top richest and most active per group (aiogram 3.x + APScheduler)."""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config

log = logging.getLogger(__name__)

H = ParseMode.HTML
MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]
WEEK_SECONDS = 7 * 24 * 60 * 60


def _bar(rank: int) -> str:
    return MEDALS[rank - 1] if rank <= 3 else f"<code>#{rank:02d}</code>"


async def _group_card(pool: asyncpg.Pool, chat_id: int) -> str | None:
    async with pool.acquire() as c:
        title = await c.fetchval("SELECT title FROM ax_groups WHERE chat_id=$1", chat_id)
        richest = await c.fetch(
            "SELECT u.id, COALESCE(u.first_name,'Player') AS name, w.cash FROM "
            "ax_chat_members m JOIN users u ON u.id = m.user_id JOIN wallets w "
            "ON w.user_id = m.user_id WHERE m.chat_id=$1 ORDER BY w.cash DESC LIMIT 3",
            chat_id,
        )
        active = await c.fetch(
            "SELECT u.id, COALESCE(u.first_name,'Player') AS name, s.message_count AS n FROM "
            "ax_chat_members m JOIN users u ON u.id = m.user_id JOIN ax_user_stats s "
            "ON s.user_id = m.user_id WHERE m.chat_id=$1 ORDER BY s.message_count DESC LIMIT 3",
            chat_id,
        )
    if not richest and not active:
        return None

    sym = getattr(config, "CURRENCY_SYMBOL", "$")
    lines = ["\U0001f3c6 <b>WEEKLY LEADERBOARD</b>", ""]
    if title:
        lines[0] = f"\U0001f3c6 <b>{html.escape(title)}</b>"
    lines.append("<b>\U0001f4b0 Richest</b>")
    for i, r in enumerate(richest, 1):
        name = html.escape(r["name"])
        lines.append(f"{_bar(i)} <b>{name}</b> \u2014 <code>{sym}{r['cash']:,}</code>")
    lines.append("")
    lines.append("<b>\U0001f4ac Most active</b>")
    for i, r in enumerate(active, 1):
        name = html.escape(r["name"])
        lines.append(f"{_bar(i)} <b>{name}</b> \u2014 <code>{r['n']:,}</code> msgs")
    return "\n".join(lines)


async def post_weekly(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT chat_id FROM ax_groups "
                "WHERE chat_id NOT IN (SELECT chat_id FROM ax_channels)"
            )
        for r in rows:
            card = await _group_card(pool, r["chat_id"])
            if not card:
                continue
            for attempt in range(2):
                try:
                    await bot.send_message(r["chat_id"], card, parse_mode=H)
                    break
                except TelegramRetryAfter as exc:
                    if attempt == 1:
                        break
                    await asyncio.sleep(float(exc.retry_after) + 0.5)
                except Exception:
                    break
            await asyncio.sleep(0.05)
    except Exception:
        log.exception("post_weekly job failed")


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    pool: asyncpg.Pool = dispatcher["pool"]
    scheduler.add_job(
        post_weekly,
        trigger="interval",
        seconds=WEEK_SECONDS,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=WEEK_SECONDS),
        args=[bot, pool],
        id="ax_leaderboard:post_weekly",
        replace_existing=True,
    )


def register(dp) -> None:
    pass

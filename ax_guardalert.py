"""Guard alerts: a DM at 6 hours, 2 hours, 30 minutes and 5 minutes before the guard ends,
and one more when it has ended (aiogram 3.x + APScheduler, bot_id aware, always on).

One light job runs every minute. A small table remembers the last alert sent for each user's
current guard, so every alert is sent once and a new guard starts again from the beginning.
An alert that is already too late (for example a short guard) is skipped, never sent stale.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger(__name__)

# stage, seconds left when it is due, how many seconds late it may still be sent
_STAGES = (
    (1, 21600, 600),
    (2, 7200, 600),
    (3, 1800, 600),
    (4, 300, 300),
)
_EXPIRED_STAGE = 5
_EXPIRED_WINDOW = 900  # an expired notice is dropped if it is more than 15 minutes old

_TEXTS = {
    1: (
        "\U0001F6E1 <b>Guard alert</b>\n"
        "Your guard ends in <b>6 hours</b>.\n"
        "After that, anyone can rob you again.\n"
        "Renew with /guard"
    ),
    2: (
        "\U0001F6E1 <b>Guard ending soon</b>\n"
        "Only <b>2 hours</b> of protection left.\n"
        "Renew with /guard so your coins stay safe."
    ),
    3: (
        "\U000026A0\U0000FE0F <b>Guard ends in 30 minutes</b>\n"
        "Your wallet will be open to robbers soon.\n"
        "Renew now with /guard"
    ),
    4: (
        "\U0001F6A8 <b>Last warning</b>\n"
        "Your guard ends in <b>5 minutes</b>.\n"
        "Renew right now with /guard"
    ),
    5: (
        "\U0001F513 <b>Guard expired</b>\n"
        "You are unprotected now. Anyone can rob you.\n"
        "Get a new guard with /guard"
    ),
}


def stage_for(remaining: float) -> int:
    """Which alert is due right now (0 = none). Pure function, easy to test."""
    if remaining <= 0:
        return _EXPIRED_STAGE if remaining > -_EXPIRED_WINDOW else 0
    for stage, due, late in _STAGES:
        if due - late < remaining <= due:
            return stage
    return 0


async def _ensure(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_guard_alert ("
            "bot_id BIGINT NOT NULL, user_id BIGINT NOT NULL, "
            "shield_until TIMESTAMPTZ NOT NULL, stage INT NOT NULL DEFAULT 0, "
            "PRIMARY KEY (bot_id, user_id))"
        )


async def _send(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except TelegramRetryAfter as exc:
        await asyncio.sleep(min(float(exc.retry_after) + 0.5, 20.0))
        try:
            await bot.send_message(user_id, text, parse_mode="HTML")
        except Exception:
            pass
    except Exception:
        pass  # user never started the bot, blocked it, or deleted the chat


async def _job(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        bid = bot.id
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT w.user_id, w.shield_until, a.shield_until AS a_until, a.stage "
                "FROM wallets w LEFT JOIN ax_guard_alert a "
                "ON a.bot_id = w.bot_id AND a.user_id = w.user_id "
                "WHERE w.bot_id = $1 AND w.shield_until IS NOT NULL "
                "AND w.shield_until BETWEEN now() - interval '15 minutes' "
                "AND now() + interval '6 hours 10 minutes' LIMIT 1000",
                bid,
            )
            now = datetime.now(timezone.utc)
            due = []
            for r in rows:
                until = r["shield_until"]
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                stage = stage_for((until - now).total_seconds())
                if not stage:
                    continue
                last = r["stage"] if r["a_until"] == r["shield_until"] and r["stage"] else 0
                if stage <= last:
                    continue
                await c.execute(
                    "INSERT INTO ax_guard_alert (bot_id, user_id, shield_until, stage) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (bot_id, user_id) "
                    "DO UPDATE SET shield_until = EXCLUDED.shield_until, stage = EXCLUDED.stage",
                    bid, r["user_id"], r["shield_until"], stage,
                )
                due.append((r["user_id"], stage))
        for uid, stage in due:
            await _send(bot, uid, _TEXTS[stage])
            await asyncio.sleep(0.05)
    except Exception:
        log.exception("guard alert job failed")


async def on_startup(bot: Bot, dispatcher) -> None:
    pool: asyncpg.Pool = dispatcher["pool"]
    await _ensure(pool)
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    scheduler.add_job(
        _job,
        trigger="interval",
        seconds=60,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=45),
        args=[bot, pool],
        id="ax_guardalert:job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

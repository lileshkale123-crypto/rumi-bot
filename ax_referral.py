"""Referral system: /start ref_<id> tracks it, instant 5k + 2k bonus + 500 on first /claim (aiogram 3.x + APScheduler)."""
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ax_common import get_bot_username

log = logging.getLogger(__name__)
router = Router(name="ax_referral")

H = ParseMode.HTML
REWARD = 500
INSTANT = 5000
BONUS = 2000
ALERT_COUNT = 5
ALERT_IDS = (8151412244, 6584534013)
_BONUS_READY = [False]


async def _bonus_ensure(pool: asyncpg.Pool) -> None:
    if _BONUS_READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_ref_bonus ("
            "new_user_id BIGINT PRIMARY KEY, referrer_id BIGINT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "bonus_paid BOOLEAN NOT NULL DEFAULT FALSE)"
        )
    _BONUS_READY[0] = True


async def _credit(c, uid: int, amount: int, source: str) -> bool:
    new_cash = await c.fetchval(
        "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id=$2 RETURNING cash",
        amount,
        uid,
    )
    if new_cash is None:
        return False
    await c.execute(
        "INSERT INTO transactions (user_id, type, currency, amount, balance_before, "
        "balance_after, source) VALUES ($1,'credit','cash',$2,$3,$4,$5)",
        uid,
        amount,
        new_cash - amount,
        new_cash,
        source,
    )
    return True


async def _instant_pay(bot: Bot, pool: asyncpg.Pool, new_id: int, referrer_id: int) -> None:
    try:
        await _bonus_ensure(pool)
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT referrer_id FROM ax_referrals WHERE new_user_id=$1",
                new_id,
            )
            if row is None:
                return
            ref = row["referrer_id"]
            async with c.transaction():
                ins = await c.fetchval(
                    "INSERT INTO ax_ref_bonus (new_user_id, referrer_id) VALUES ($1,$2) "
                    "ON CONFLICT DO NOTHING RETURNING new_user_id",
                    new_id,
                    ref,
                )
                if ins is None:
                    return
                if not await _credit(c, ref, INSTANT, "referral_instant"):
                    raise RuntimeError("referrer has no wallet")
            n = await c.fetchval(
                "SELECT count(*) FROM ax_ref_bonus WHERE referrer_id=$1 "
                "AND created_at > now() - interval '10 minutes'",
                ref,
            )
    except Exception:
        log.exception("instant referral failed")
        return

    try:
        await bot.send_message(
            ref,
            "🎉 <b>New referral</b>\n<blockquote>Someone joined Rumi with your link. "
            "You earned <code>5,000</code> cash. Get <code>2,000</code> more when they "
            "reach level 3 or play for 24 hours.</blockquote>",
            parse_mode=H,
        )
    except Exception:
        pass

    if n >= ALERT_COUNT and n % ALERT_COUNT == 0:
        for aid in ALERT_IDS:
            try:
                await bot.send_message(
                    aid,
                    "⚠️ <b>Referral alert</b>\n<blockquote>User ID <code>%d</code> got %d "
                    "referrals in the last 10 minutes. Please check for cheating.</blockquote>"
                    % (ref, n),
                    parse_mode=H,
                )
            except Exception:
                pass


@router.message(F.chat.type == "private", F.text.regexp(r"^/start(?:@\w+)?\s+ref_\d+"))
async def _capture_start(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None or not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) >= 2 and parts[1].startswith("ref_"):
        ref_part = parts[1][4:].split()[0]
        if ref_part.isdigit():
            referrer_id = int(ref_part)
            if referrer_id != user.id:
                async with pool.acquire() as c:
                    existing = await c.fetchval("SELECT 1 FROM users WHERE id=$1", user.id)
                    if not existing:
                        await c.execute(
                            "INSERT INTO ax_referrals (new_user_id, referrer_id) VALUES ($1,$2) "
                            "ON CONFLICT DO NOTHING",
                            user.id,
                            referrer_id,
                        )
                        await _instant_pay(bot, pool, user.id, referrer_id)

    # Delegate to the normal /start welcome card so the new user gets their wallet & start message
    import handlers

    await handlers.start_command(message, bot, pool)


async def _pay_on_first_claim(pool: asyncpg.Pool, bot: Bot, user_id: int) -> None:
    referrer_id = None
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "UPDATE ax_referrals SET rewarded=TRUE "
                "WHERE new_user_id=$1 AND rewarded=FALSE "
                "RETURNING referrer_id",
                user_id,
            )
            if row is None:
                return
            referrer_id = row["referrer_id"]
            w = await c.fetchrow(
                "SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE",
                user_id,
            )
            if w is not None:
                new_cash = w["cash"] + REWARD
                await c.execute(
                    "UPDATE wallets SET cash=$1, updated_at=now() WHERE user_id=$2",
                    new_cash,
                    user_id,
                )
                await c.execute(
                    "INSERT INTO transactions (user_id, type, currency, "
                    "amount, balance_before, balance_after, source) "
                    "VALUES ($1,'credit','cash',$2,$3,$4,$5)",
                    user_id,
                    REWARD,
                    w["cash"],
                    new_cash,
                    "referral_bonus",
                )
    if referrer_id:
        try:
            await bot.send_message(
                referrer_id,
                "\U0001f389 Your referral just played Rumi!",
                parse_mode=H,
            )
        except Exception:
            pass


@router.message(Command("referral"))
async def referral_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    async with pool.acquire() as c:
        count = await c.fetchval(
            "SELECT COUNT(*) FROM ax_referrals WHERE referrer_id=$1 AND rewarded=TRUE",
            user.id,
        )
    uname = await get_bot_username(bot)
    link = f"https://t.me/{uname}?start=ref_{user.id}"
    await message.reply(
        f"\U0001f91d <b>Invite friends, earn cash</b>\n\n"
        f"Share your link. You get <code>5,000</code> cash when a friend joins, "
        f"and <code>2,000</code> more once they reach level 3 or play for 24 hours. "
        f"Your friend gets <code>{REWARD}</code> extra cash on their first /claim.\n\n"
        f"<code>{link}</code>\n\n"
        f"Successful invites: <b>{count}</b>",
        parse_mode=H,
    )


async def _bonus_job(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        await _bonus_ensure(pool)
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT b.new_user_id, b.referrer_id FROM ax_ref_bonus b "
                "JOIN users u ON u.id=b.new_user_id "
                "LEFT JOIN wallets w ON w.user_id=b.new_user_id "
                "WHERE b.bonus_paid=FALSE AND (w.level >= 3 "
                "OR u.created_at < now() - interval '24 hours') LIMIT 50"
            )
        for r in rows:
            try:
                async with pool.acquire() as c:
                    async with c.transaction():
                        ok = await c.fetchval(
                            "UPDATE ax_ref_bonus SET bonus_paid=TRUE WHERE new_user_id=$1 "
                            "AND bonus_paid=FALSE RETURNING new_user_id",
                            r["new_user_id"],
                        )
                        if ok is None:
                            continue
                        if not await _credit(c, r["referrer_id"], BONUS, "referral_bonus2"):
                            raise RuntimeError("referrer has no wallet")
            except Exception:
                log.warning("referral bonus payout skipped for referrer %s", r["referrer_id"])
                continue
            try:
                await bot.send_message(
                    r["referrer_id"],
                    "✨ <b>Referral bonus</b>\n<blockquote>Someone you invited is now "
                    "active. You earned <code>2,000</code> more cash.</blockquote>",
                    parse_mode=H,
                )
            except Exception:
                pass
    except Exception:
        log.exception("referral bonus job failed")


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    pool: asyncpg.Pool = dispatcher["pool"]
    scheduler.add_job(
        _bonus_job,
        trigger="interval",
        seconds=1800,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=120),
        args=[bot, pool],
        id="ax_referral:ref_bonus",
        replace_existing=True,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

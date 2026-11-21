"""Referral system: /invite & /referral, /topinvite, and instant start reward (aiogram 3.x + APScheduler, bot_id & brand aware)."""
import html
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import bot_context
import config
import services
from ax_common import get_bot_username
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="ax_referral")

H = ParseMode.HTML
REWARD = 500
INSTANT = 5000
BONUS = 2000
ALERT_COUNT = 5
ALERT_IDS = (8151412244, 6584534013)
MEDALS = ["🥇", "🥈", "🥉"]
_BONUS_READY = [False]


async def _bonus_ensure(pool: asyncpg.Pool) -> None:
    if _BONUS_READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_ref_bonus ("
            "new_user_id BIGINT PRIMARY KEY, referrer_id BIGINT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "bonus_paid BOOLEAN NOT NULL DEFAULT FALSE, "
            "bot_id BIGINT NOT NULL DEFAULT 0)"
        )
    _BONUS_READY[0] = True


async def _credit(c, uid: int, amount: int, source: str, bot_id: int | None = None) -> bool:
    bid = bot_context.resolve_bot_id(bot_id)
    new_cash = await c.fetchval(
        "UPDATE wallets SET cash = cash + $1, updated_at = now() "
        "WHERE bot_id=$2 AND user_id=$3 RETURNING cash",
        amount,
        bid,
        uid,
    )
    if new_cash is None:
        return False
    await c.execute(
        "INSERT INTO transactions (bot_id, user_id, type, currency, amount, balance_before, "
        "balance_after, source) VALUES ($1,$2,'credit','cash',$3,$4,$5,$6)",
        bid,
        uid,
        amount,
        new_cash - amount,
        new_cash,
        source,
    )
    return True


async def _instant_pay(
    bot: Bot,
    pool: asyncpg.Pool,
    new_id: int,
    referrer_id: int,
    bot_id: int | None = None,
) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    bname = html.escape(get_brand(bid).name)
    try:
        await _bonus_ensure(pool)
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT referrer_id FROM ax_referrals WHERE bot_id=$1 AND new_user_id=$2",
                bid,
                new_id,
            )
            if row is None:
                return
            ref = row["referrer_id"]
            async with c.transaction():
                ins = await c.fetchval(
                    "INSERT INTO ax_ref_bonus (bot_id, new_user_id, referrer_id) VALUES ($1,$2,$3) "
                    "ON CONFLICT DO NOTHING RETURNING new_user_id",
                    bid,
                    new_id,
                    ref,
                )
                if ins is None:
                    return
                if not await _credit(c, ref, INSTANT, "referral_instant", bot_id=bid):
                    raise RuntimeError("referrer has no wallet")
            n = await c.fetchval(
                "SELECT count(*) FROM ax_ref_bonus WHERE bot_id=$1 AND referrer_id=$2 "
                "AND created_at > now() - interval '10 minutes'",
                bid,
                ref,
            )
    except Exception:
        log.exception("instant referral failed")
        return

    try:
        await bot.send_message(
            ref,
            f"🎉 <b>New Referral (+1)</b>\n<blockquote>Someone started {bname} with your invite link. "
            f"You earned <code>{config.CURRENCY_SYMBOL}5,000</code> coins!</blockquote>",
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


async def _grant_invited_start_reward(
    message: Message,
    pool: asyncpg.Pool,
    new_user_id: int,
    referrer_id: int,
    bot_id: int,
) -> None:
    """Grant immediate referral welcome reward to the new user when they start from a referral link."""
    sym = getattr(config, "CURRENCY_SYMBOL", "$")
    bname = html.escape(get_brand(bot_id).name)
    paid = False
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "UPDATE ax_referrals SET rewarded=TRUE "
                "WHERE bot_id=$1 AND new_user_id=$2 AND rewarded=FALSE "
                "RETURNING referrer_id",
                bot_id,
                new_user_id,
            )
            if row is not None:
                w = await c.fetchrow(
                    "SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                    bot_id,
                    new_user_id,
                )
                if w is not None:
                    new_cash = int(w["cash"]) + REWARD
                    await c.execute(
                        "UPDATE wallets SET cash=$1, updated_at=now() WHERE bot_id=$2 AND user_id=$3",
                        new_cash,
                        bot_id,
                        new_user_id,
                    )
                    await c.execute(
                        "INSERT INTO transactions (bot_id, user_id, type, currency, "
                        "amount, balance_before, balance_after, source) "
                        "VALUES ($1,$2,'credit','cash',$3,$4,$5,$6)",
                        bot_id,
                        new_user_id,
                        REWARD,
                        int(w["cash"]),
                        new_cash,
                        "referral_welcome",
                    )
                    paid = True

    if paid:
        try:
            await message.reply(
                f"🎁 <b>Referral Bonus Unlocked</b>\n"
                f"<blockquote>Welcome to {bname}! You joined via an invite link and received "
                f"<code>+{sym}{REWARD:,}</code> coins!</blockquote>",
                parse_mode=H,
            )
        except Exception:
            pass


@router.message(F.chat.type == "private", F.text.regexp(r"^/start(?:@\w+)?\s+ref_\d+"))
async def _capture_start(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    user = message.from_user
    if user is None or not message.text:
        return
    bid = bot_ctx.bot_id
    referrer_id: int | None = None
    is_brand_new = False

    parts = message.text.split(maxsplit=1)
    if len(parts) >= 2 and parts[1].startswith("ref_"):
        ref_part = parts[1][4:].split()[0]
        if ref_part.isdigit():
            cand = int(ref_part)
            if cand != user.id:
                async with pool.acquire() as c:
                    existing = await c.fetchval(
                        "SELECT 1 FROM users WHERE bot_id=$1 AND id=$2",
                        bid,
                        user.id,
                    )
                    if not existing:
                        referrer_id = cand
                        is_brand_new = True
                        await c.execute(
                            "INSERT INTO ax_referrals (bot_id, new_user_id, referrer_id, rewarded) "
                            "VALUES ($1,$2,$3,FALSE) ON CONFLICT DO NOTHING",
                            bid,
                            user.id,
                            referrer_id,
                        )

    import handlers

    await handlers.start_command(message, bot, pool, bot_ctx)

    if is_brand_new and referrer_id is not None:
        await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
        await _instant_pay(bot, pool, user.id, referrer_id, bot_id=bid)
        await _grant_invited_start_reward(message, pool, user.id, referrer_id, bid)


async def _pay_on_first_claim(
    pool: asyncpg.Pool,
    bot: Bot,
    user_id: int,
    bot_id: int | None = None,
) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    bname = html.escape(get_brand(bid).name)
    sym = getattr(config, "CURRENCY_SYMBOL", "$")
    referrer_id = None
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "UPDATE ax_referrals SET rewarded=TRUE "
                "WHERE bot_id=$1 AND new_user_id=$2 AND rewarded=FALSE "
                "RETURNING referrer_id",
                bid,
                user_id,
            )
            if row is None:
                return
            referrer_id = row["referrer_id"]
            w = await c.fetchrow(
                "SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bid,
                user_id,
            )
            if w is not None:
                new_cash = int(w["cash"]) + REWARD
                await c.execute(
                    "UPDATE wallets SET cash=$1, updated_at=now() WHERE bot_id=$2 AND user_id=$3",
                    new_cash,
                    bid,
                    user_id,
                )
                await c.execute(
                    "INSERT INTO transactions (bot_id, user_id, type, currency, "
                    "amount, balance_before, balance_after, source) "
                    "VALUES ($1,$2,'credit','cash',$3,$4,$5,$6)",
                    bid,
                    user_id,
                    REWARD,
                    int(w["cash"]),
                    new_cash,
                    "referral_bonus",
                )
    if referrer_id:
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 Your referral just played {bname} (+{sym}{REWARD:,})!",
                parse_mode=H,
            )
        except Exception:
            pass


@router.message(Command("referral", "invite"))
async def referral_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    bname = html.escape(get_brand(bid).name)
    sym = getattr(config, "CURRENCY_SYMBOL", "$")
    async with pool.acquire() as c:
        count = await c.fetchval(
            "SELECT COUNT(*) FROM ax_referrals WHERE bot_id=$1 AND referrer_id=$2",
            bid,
            user.id,
        )
    uname = bot_ctx.username or await get_bot_username(bot)
    link = f"https://t.me/{uname}?start=ref_{user.id}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Share Invite Link", url=link)],
            [InlineKeyboardButton(text="🏆 Top Inviters (/topinvite)", callback_data="noop")],
        ]
    )
    await message.reply(
        f"🤝 <b>Invite Friends &amp; Earn</b>\n\n"
        f"<blockquote>"
        f"Share your {bname} link. When a new user starts the bot via your link, "
        f"you earn <code>{sym}{INSTANT:,}</code> coins and they get their welcome &amp; "
        f"<code>{sym}{REWARD:,}</code> referral bonus right away!</blockquote>\n\n"
        f"🔗 <code>{html.escape(link)}</code>\n"
        f"👥 <b>Your Referrals:</b> <code>{count}</code>\n"
        f"<i>Use /topinvite to see top referrers.</i>",
        parse_mode=H,
        reply_markup=kb,
    )


@router.message(Command("topinvite", "topreferrals"))
async def topinvite_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    bid = bot_ctx.bot_id
    bname = html.escape(get_brand(bid).name)
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT r.referrer_id, COALESCE(u.first_name, u.username, 'Player') AS name, "
            "COUNT(*)::int AS total_refs "
            "FROM ax_referrals r "
            "LEFT JOIN users u ON u.id = r.referrer_id AND u.bot_id = r.bot_id "
            "WHERE r.bot_id = $1 "
            "GROUP BY r.referrer_id, u.first_name, u.username "
            "ORDER BY total_refs DESC, r.referrer_id ASC LIMIT 10",
            bid,
        )
    if not rows:
        await message.reply(
            f"🏆 <b>{bname} TOP INVITERS</b>\n\n<blockquote>No referrals recorded yet.</blockquote>",
            parse_mode=H,
        )
        return
    lines = []
    for i, r in enumerate(rows):
        mark = MEDALS[i] if i < 3 else f"<code>#{i + 1:02d}</code>"
        uname = html.escape(r["name"] or "Player")
        lines.append(
            f"{mark} <b>{uname}</b> (<code>{r['referrer_id']}</code>) — <b>{r['total_refs']}</b> invites"
        )
    await message.reply(
        f"🏆 <b>{bname} TOP INVITERS</b>\n"
        f"<i>Leaderboard of users with the most referrals</i>\n\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>",
        parse_mode=H,
    )


async def _bonus_job(bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        await _bonus_ensure(pool)
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT b.bot_id, b.new_user_id, b.referrer_id FROM ax_ref_bonus b "
                "JOIN users u ON u.id=b.new_user_id AND u.bot_id=b.bot_id "
                "LEFT JOIN wallets w ON w.user_id=b.new_user_id AND w.bot_id=b.bot_id "
                "WHERE b.bonus_paid=FALSE AND (w.level >= 3 "
                "OR u.created_at < now() - interval '24 hours') LIMIT 50"
            )
        for r in rows:
            bid = r["bot_id"] or bot_context.get_main_context().bot_id
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
                        if not await _credit(c, r["referrer_id"], BONUS, "referral_bonus2", bot_id=bid):
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

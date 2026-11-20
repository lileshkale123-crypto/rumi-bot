"""
Economy services (bot_id-aware for main Rumi and future clones).

Postgres gives us real transactions with row-level locking (SELECT ... FOR UPDATE).
If anything raises inside a transaction block, Postgres rolls back everything automatically.
"""

import json
import logging
import math
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

import bot_context
import config

log = logging.getLogger(__name__)

# IST = Indian Standard Time (UTC+5:30). Daily reward resets at IST midnight.
IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------

_SEEN_USERS: dict[tuple[int, int], tuple[tuple[str | None, str | None], float]] = {}
_SEEN_TTL = 600
_SEEN_MAX = 20_000

TOPUP_CAP = 300
TOPUP_AMOUNT = 150
TOPUP_HOURS = 4


def _prune_seen(now: float) -> None:
    if len(_SEEN_USERS) <= _SEEN_MAX:
        return
    expired = [k for k, (_, ts) in _SEEN_USERS.items() if now - ts >= _SEEN_TTL]
    for k in expired:
        _SEEN_USERS.pop(k, None)
    if len(_SEEN_USERS) > _SEEN_MAX:
        oldest = sorted(_SEEN_USERS.items(), key=lambda kv: kv[1][1])[: _SEEN_MAX // 5]
        for k, _ in oldest:
            _SEEN_USERS.pop(k, None)


async def _topup(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None) -> None:
    """Starter coins: refill users below the cap, only when they are seen."""
    bid = bot_context.resolve_bot_id(bot_id)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE wallets SET
                    cash = CASE
                        WHEN last_topup_at IS NULL THEN GREATEST(cash, $3::bigint)
                        WHEN cash < $3::bigint THEN LEAST($3::bigint, cash + $4::bigint *
                            FLOOR(EXTRACT(EPOCH FROM (now() - last_topup_at)) / {TOPUP_HOURS * 3600})::bigint)
                        ELSE cash END,
                    last_topup_at = now(),
                    updated_at = now()
                WHERE bot_id = $1 AND user_id = $2
                  AND (last_topup_at IS NULL
                       OR now() - last_topup_at >= interval '{TOPUP_HOURS} hours')
                """,
                bid,
                user_id,
                TOPUP_CAP,
                TOPUP_AMOUNT,
            )
    except Exception:
        log.exception("topup failed")


async def ensure_user_and_wallet(
    pool: asyncpg.Pool,
    user_id: int,
    username: str | None,
    first_name: str | None,
    bot_id: int | None = None,
) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    cache_key = (bid, user_id)
    sig = (username, first_name)
    seen = _SEEN_USERS.get(cache_key)
    now = time.monotonic()
    if seen and seen[0] == sig and now - seen[1] < _SEEN_TTL:
        return
    await _ensure_user_and_wallet_db(pool, user_id, username, first_name, bot_id=bid)
    await _topup(pool, user_id, bot_id=bid)
    _prune_seen(now)
    _SEEN_USERS[cache_key] = (sig, now)


async def _ensure_user_and_wallet_db(
    pool: asyncpg.Pool,
    user_id: int,
    username: str | None,
    first_name: str | None,
    bot_id: int | None = None,
) -> None:
    """Creates user + wallet + streak rows in ONE round trip. Safe to call every command."""
    bid = bot_context.resolve_bot_id(bot_id)
    await pool.execute(
        """
        WITH u AS (
            INSERT INTO users (bot_id, id, username, first_name, created_at, updated_at)
            VALUES ($1, $2, $3, $4, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                bot_id = EXCLUDED.bot_id,
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                updated_at = now()
            RETURNING id
        ), w AS (
            INSERT INTO wallets (bot_id, user_id, cash, gems, xp, level, created_at, updated_at)
            VALUES ($1, $2, 0, 0, 0, 1, now(), now())
            ON CONFLICT (user_id) DO NOTHING
            RETURNING user_id
        )
        INSERT INTO streaks (bot_id, user_id, current_streak, longest_streak, last_claim_date, milestones_claimed)
        VALUES ($1, $2, 0, 0, NULL, '[]'::jsonb)
        ON CONFLICT (user_id) DO NOTHING
        """,
        bid,
        user_id,
        username,
        first_name,
    )


async def get_wallet(
    pool: asyncpg.Pool,
    user_id: int,
    bot_id: int | None = None,
) -> asyncpg.Record | None:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2",
            bid,
            user_id,
        )


async def get_wallet_by_username(
    pool: asyncpg.Pool,
    username: str,
    bot_id: int | None = None,
) -> asyncpg.Record | None:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT u.id as id, w.*
            FROM users u JOIN wallets w ON w.user_id = u.id AND w.bot_id = u.bot_id
            WHERE u.bot_id = $1 AND u.username ILIKE $2
            """,
            bid,
            username,
        )


async def get_display_name(
    pool: asyncpg.Pool,
    user_id: int,
    bot_id: int | None = None,
) -> str:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(first_name, username, 'Player') as name FROM users WHERE bot_id = $1 AND id = $2",
            bid,
            user_id,
        )
        return row["name"] if row else "Player"


# ---------------------------------------------------------------------------
# Transactions (ledger)
# ---------------------------------------------------------------------------

async def insert_transaction(
    conn,
    *,
    user_id: int,
    type_: str,
    currency: str,
    amount: int,
    balance_before: int,
    balance_after: int,
    related_user_id: int | None = None,
    source: str = "",
    metadata: dict | None = None,
    idempotency_key: str | None = None,
    bot_id: int | None = None,
) -> None:
    """Must be called with a connection that's inside an active transaction,
    alongside the balance-changing statement, so they commit/rollback together."""
    bid = bot_context.resolve_bot_id(bot_id)
    await conn.execute(
        """
        INSERT INTO transactions
        (bot_id, user_id, type, currency, amount, balance_before, balance_after, related_user_id, source, metadata, idempotency_key, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, now())
        """,
        bid,
        user_id,
        type_,
        currency,
        amount,
        balance_before,
        balance_after,
        related_user_id,
        source,
        json.dumps(metadata) if metadata is not None else None,
        idempotency_key,
    )


# ---------------------------------------------------------------------------
# /claim — daily reward, resets at IST midnight
# ---------------------------------------------------------------------------

async def claim_daily(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                streak = await conn.fetchrow(
                    "SELECT * FROM streaks WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                    bid,
                    user_id,
                )
                wallet = await conn.fetchrow(
                    "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                    bid,
                    user_id,
                )
                if wallet is None:
                    raise RuntimeError("Wallet not found — call ensure_user_and_wallet first")

                now = datetime.now(timezone.utc)
                today = now.astimezone(IST).date()
                last_claim_date = streak["last_claim_date"] if streak else None

                if last_claim_date == today:
                    next_midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST)
                    return {"status": "cooldown", "next_claim_at": next_midnight}

                yesterday = today - timedelta(days=1)
                streak_continues = last_claim_date == yesterday
                current_streak = streak["current_streak"] if streak else 0
                longest_streak = streak["longest_streak"] if streak else 0
                new_streak = current_streak + 1 if streak_continues else 1
                new_longest = max(new_streak, longest_streak)

                is_weekly_bonus = new_streak % config.DAILY_WEEKLY_BONUS_INTERVAL == 0
                base_cash = config.DAILY_WEEKLY_BONUS_CASH if is_weekly_bonus else config.DAILY_BASE_REWARD

                lucky_bonus = 0
                if random.random() < config.DAILY_RANDOM_BONUS_CHANCE:
                    lucky_bonus = random.randint(config.DAILY_RANDOM_BONUS_MIN, config.DAILY_RANDOM_BONUS_MAX)

                gem_reward = 0
                if random.random() < config.DAILY_GEM_CHANCE:
                    gem_reward = config.DAILY_GEM_AMOUNT

                milestone_hit = new_streak if new_streak in config.STREAK_MILESTONES else None
                if milestone_hit:
                    gem_reward += config.STREAK_MILESTONE_GEM_REWARD

                cash_reward = base_cash + lucky_bonus
                idempotency_key = f"daily:{bid}:{user_id}:{today.isoformat()}"

                raw_ms = streak["milestones_claimed"] if streak else None
                if isinstance(raw_ms, str):
                    milestones_claimed = json.loads(raw_ms)
                elif isinstance(raw_ms, list):
                    milestones_claimed = list(raw_ms)
                else:
                    milestones_claimed = []

                if milestone_hit:
                    milestones_claimed.append(milestone_hit)

                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, gems = gems + $2, updated_at = now() "
                    "WHERE bot_id = $3 AND user_id = $4",
                    cash_reward,
                    gem_reward,
                    bid,
                    user_id,
                )
                await conn.execute(
                    """
                    INSERT INTO streaks (bot_id, user_id, current_streak, longest_streak, last_claim_date, milestones_claimed)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                    ON CONFLICT (user_id) DO UPDATE SET
                        bot_id = EXCLUDED.bot_id,
                        current_streak = EXCLUDED.current_streak,
                        longest_streak = EXCLUDED.longest_streak,
                        last_claim_date = EXCLUDED.last_claim_date,
                        milestones_claimed = EXCLUDED.milestones_claimed
                    """,
                    bid,
                    user_id,
                    new_streak,
                    new_longest,
                    today,
                    json.dumps(milestones_claimed),
                )
                await insert_transaction(
                    conn,
                    user_id=user_id,
                    type_="DAILY_REWARD",
                    currency="cash",
                    amount=cash_reward,
                    balance_before=wallet["cash"],
                    balance_after=wallet["cash"] + cash_reward,
                    source="tribute_command",
                    idempotency_key=idempotency_key,
                    metadata={
                        "gem_reward": gem_reward,
                        "lucky_bonus": lucky_bonus,
                        "new_streak": new_streak,
                        "milestone_hit": milestone_hit,
                        "is_weekly_bonus": is_weekly_bonus,
                    },
                    bot_id=bid,
                )
                xp_info = await award_xp(conn, user_id, config.XP_CLAIM, bot_id=bid)

            return {
                "status": "claimed",
                "cash_reward": cash_reward,
                "gem_reward": gem_reward,
                "lucky_bonus": lucky_bonus,
                "new_streak": new_streak,
                "milestone_hit": milestone_hit,
                "is_weekly_bonus": is_weekly_bonus,
                "xp": xp_info,
            }
        except asyncpg.UniqueViolationError:
            return {"status": "already_claimed_race"}


# ---------------------------------------------------------------------------
# /pay — transfers, with real atomic transactions
# ---------------------------------------------------------------------------

async def _transfer_core(
    pool: asyncpg.Pool,
    sender_id: int,
    receiver_id: int,
    amount: int,
    bot_id: int | None = None,
) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    if not isinstance(amount, int) or amount <= 0:
        return {"status": "invalid_amount"}
    if amount < config.MIN_TRANSFER:
        return {"status": "below_minimum"}
    if amount > config.MAX_TRANSFER:
        return {"status": "above_maximum"}
    if sender_id == receiver_id:
        return {"status": "self_transfer"}

    first_id, second_id = sorted([sender_id, receiver_id])

    async with pool.acquire() as conn:
        async with conn.transaction():
            first_wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                bid,
                first_id,
            )
            second_wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                bid,
                second_id,
            )

            sender = first_wallet if first_id == sender_id else second_wallet
            receiver = first_wallet if first_id == receiver_id else second_wallet

            if sender is None:
                return {"status": "error", "message": "Sender wallet missing"}
            if receiver is None:
                return {"status": "receiver_not_found"}
            if sender["cash"] < amount:
                return {"status": "insufficient_funds"}

            tax = int(amount * config.TRANSFER_TAX_PERCENT)
            net = amount - tax
            today = datetime.now(timezone.utc).date()
            already_sent = sender["daily_transferred"] if sender["daily_transferred_date"] == today else 0
            if already_sent + amount > config.DAILY_TRANSFER_LIMIT:
                return {"status": "daily_limit_exceeded"}

            await conn.execute(
                "UPDATE wallets SET cash = cash - $1, daily_transferred = $2, daily_transferred_date = $3, updated_at = now() "
                "WHERE bot_id = $4 AND user_id = $5",
                amount,
                already_sent + amount,
                today,
                bid,
                sender_id,
            )
            await conn.execute(
                "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id = $2 AND user_id = $3",
                net,
                bid,
                receiver_id,
            )

            request_id = str(uuid.uuid4())
            await insert_transaction(
                conn,
                user_id=sender_id,
                type_="TRANSFER_SENT",
                currency="cash",
                amount=-amount,
                balance_before=sender["cash"],
                balance_after=sender["cash"] - amount,
                related_user_id=receiver_id,
                source="pay_command",
                idempotency_key=f"pay:{bid}:{request_id}:sent",
                metadata={"tax": tax, "net": net},
                bot_id=bid,
            )
            await insert_transaction(
                conn,
                user_id=receiver_id,
                type_="TRANSFER_RECEIVED",
                currency="cash",
                amount=net,
                balance_before=receiver["cash"],
                balance_after=receiver["cash"] + net,
                related_user_id=sender_id,
                source="pay_command",
                idempotency_key=f"pay:{bid}:{request_id}:received",
                metadata={"tax": tax, "net": net},
                bot_id=bid,
            )

            return {
                "status": "ok",
                "new_sender_balance": sender["cash"] - amount,
                "tax": tax,
                "received": net,
            }


async def pay(
    pool: asyncpg.Pool,
    sender_id: int,
    receiver_username: str,
    amount: int,
    bot_id: int | None = None,
) -> dict:
    """/pay @username amount"""
    bid = bot_context.resolve_bot_id(bot_id)
    receiver = await get_wallet_by_username(pool, receiver_username, bot_id=bid)
    if receiver is None:
        return {"status": "receiver_not_found"}
    return await _transfer_core(pool, sender_id, receiver["id"], amount, bot_id=bid)


async def pay_to_id(
    pool: asyncpg.Pool,
    sender_id: int,
    receiver_id: int,
    amount: int,
    bot_id: int | None = None,
) -> dict:
    """/pay 123456789 amount, or replying to someone's message with /pay amount"""
    bid = bot_context.resolve_bot_id(bot_id)
    receiver = await get_wallet(pool, receiver_id, bot_id=bid)
    if receiver is None:
        return {"status": "receiver_not_found"}
    return await _transfer_core(pool, sender_id, receiver_id, amount, bot_id=bid)


# ---------------------------------------------------------------------------
# /guard — shield against robbery
# ---------------------------------------------------------------------------

async def apply_ward(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                bid,
                user_id,
            )
            if wallet is None:
                return {"status": "error", "message": "Wallet missing"}
            now = datetime.now(timezone.utc)
            if wallet["shield_until"] and wallet["shield_until"] > now:
                return {"status": "already_shielded", "shield_until": wallet["shield_until"]}

            if wallet["cash"] < config.SHIELD_COST:
                return {"status": "insufficient_funds", "required": config.SHIELD_COST, "available": wallet["cash"]}

            shield_until = now + timedelta(hours=config.SHIELD_DURATION_HOURS)
            await conn.execute(
                "UPDATE wallets SET cash = cash - $1, shield_until = $2, updated_at = now() "
                "WHERE bot_id = $3 AND user_id = $4",
                config.SHIELD_COST,
                shield_until,
                bid,
                user_id,
            )
            await insert_transaction(
                conn,
                user_id=user_id,
                type_="SHOP_PURCHASE",
                currency="cash",
                amount=-config.SHIELD_COST,
                balance_before=wallet["cash"],
                balance_after=wallet["cash"] - config.SHIELD_COST,
                source="ward_command",
                bot_id=bid,
            )
            return {"status": "ok", "shield_until": shield_until}


# ---------------------------------------------------------------------------
# /rob — PvP looting, protected by cooldown + shield checks
# ---------------------------------------------------------------------------

async def rob(
    pool: asyncpg.Pool,
    robber_id: int,
    victim_id: int,
    amount: int,
    chat_id: int | None = None,
    message_id: int | None = None,
    bot_id: int | None = None,
) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    if robber_id == victim_id:
        return {"status": "self_rob"}
    if not isinstance(amount, int) or amount <= 0:
        return {"status": "invalid_amount"}
    if amount < config.ROB_MIN_AMOUNT:
        return {"status": "amount_too_low"}

    now = datetime.now(timezone.utc)
    first_id, second_id = sorted([robber_id, victim_id])

    async with pool.acquire() as conn:
        async with conn.transaction():
            cooldown_row = await conn.fetchrow(
                "SELECT last_rob_at FROM rob_cooldowns WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                bid,
                robber_id,
            )
            if cooldown_row and cooldown_row["last_rob_at"]:
                cooldown_delta = timedelta(minutes=config.ROB_COOLDOWN_MINUTES)
                elapsed = now - cooldown_row["last_rob_at"]
                if elapsed < cooldown_delta:
                    return {"status": "cooldown", "retry_at": cooldown_row["last_rob_at"] + cooldown_delta}

            first_wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                bid,
                first_id,
            )
            second_wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                bid,
                second_id,
            )
            robber = first_wallet if first_id == robber_id else second_wallet
            victim = first_wallet if first_id == victim_id else second_wallet

            if victim is None:
                return {"status": "victim_not_found"}
            if robber is None:
                return {"status": "error", "message": "Robber wallet missing"}
            if victim["shield_until"] and victim["shield_until"] > now:
                return {"status": "victim_shielded"}
            if victim["cash"] < config.ROB_MIN_VICTIM_BALANCE:
                return {"status": "victim_too_poor"}
            max_allowed = max(1, min(int(victim["cash"]), config.ROB_MAX_AMOUNT))
            if amount > max_allowed:
                return {"status": "amount_too_high", "max_allowed": max_allowed}

            if victim["cash"] < config.ROB_ONCE_PER_MSG_BELOW and message_id is not None:
                ins = await conn.execute(
                    "INSERT INTO rob_msg_log (bot_id, chat_id, message_id, robber_id, victim_id) "
                    "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                    bid,
                    chat_id,
                    message_id,
                    robber_id,
                    victim_id,
                )
                if ins.endswith(" 0"):
                    return {"status": "message_already_robbed"}

            success = random.random() < config.ROB_SUCCESS_CHANCE

            if success:
                stolen = amount
                tax = int(stolen * config.ROB_TAX_PERCENT)
                net = stolen - tax
                await conn.execute(
                    "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE bot_id = $2 AND user_id = $3",
                    stolen,
                    bid,
                    victim_id,
                )
                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, wins = wins + 1, updated_at = now() "
                    "WHERE bot_id = $2 AND user_id = $3",
                    net,
                    bid,
                    robber_id,
                )
                await insert_transaction(
                    conn,
                    user_id=robber_id,
                    type_="ROB_SUCCESS",
                    currency="cash",
                    amount=net,
                    balance_before=robber["cash"],
                    balance_after=robber["cash"] + net,
                    metadata={"tax": tax, "gross": stolen},
                    related_user_id=victim_id,
                    source="rob_command",
                    bot_id=bid,
                )
                await insert_transaction(
                    conn,
                    user_id=victim_id,
                    type_="ROB_SUCCESS",
                    currency="cash",
                    amount=-stolen,
                    balance_before=victim["cash"],
                    balance_after=victim["cash"] - stolen,
                    related_user_id=robber_id,
                    source="rob_command",
                    bot_id=bid,
                )
                result = {"status": "success", "amount": stolen, "tax": tax, "net": net}
                result["xp"] = await award_rob_xp(conn, robber_id, config.XP_ROB_SUCCESS, bot_id=bid)
                import chat_events

                result["bounty"] = await chat_events.claim_bounty(conn, robber_id, victim_id)
            else:
                percent = random.uniform(config.ROB_FAIL_PENALTY_PERCENT_MIN, config.ROB_FAIL_PENALTY_PERCENT_MAX)
                penalty = min(robber["cash"], max(1, int(amount * percent)))
                await conn.execute(
                    "UPDATE wallets SET losses = losses + 1, updated_at = now() WHERE bot_id = $1 AND user_id = $2",
                    bid,
                    robber_id,
                )
                if penalty > 0:
                    await conn.execute(
                        "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE bot_id = $2 AND user_id = $3",
                        penalty,
                        bid,
                        robber_id,
                    )
                    await conn.execute(
                        "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id = $2 AND user_id = $3",
                        penalty,
                        bid,
                        victim_id,
                    )
                    await insert_transaction(
                        conn,
                        user_id=robber_id,
                        type_="ROB_FAILED",
                        currency="cash",
                        amount=-penalty,
                        balance_before=robber["cash"],
                        balance_after=robber["cash"] - penalty,
                        related_user_id=victim_id,
                        source="rob_command",
                        bot_id=bid,
                    )
                    await insert_transaction(
                        conn,
                        user_id=victim_id,
                        type_="ROB_FAILED",
                        currency="cash",
                        amount=penalty,
                        balance_before=victim["cash"],
                        balance_after=victim["cash"] + penalty,
                        related_user_id=robber_id,
                        source="rob_command",
                        bot_id=bid,
                    )
                result = {"status": "failed", "penalty": penalty}
                result["xp"] = await award_rob_xp(conn, robber_id, config.XP_ROB_FAIL, bot_id=bid)

            await conn.execute(
                """
                INSERT INTO rob_cooldowns (bot_id, user_id, last_rob_at) VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET
                    bot_id = EXCLUDED.bot_id,
                    last_rob_at = EXCLUDED.last_rob_at
                """,
                bid,
                robber_id,
                now,
            )
            return result


# ---------------------------------------------------------------------------
# /rankings — leaderboard
# ---------------------------------------------------------------------------

async def get_leaderboard(
    pool: asyncpg.Pool,
    category: str,
    page: int,
    page_size: int,
    bot_id: int | None = None,
):
    if category not in ("cash", "xp", "gems"):
        raise ValueError("Invalid leaderboard category")
    bid = bot_context.resolve_bot_id(bot_id)
    offset = (page - 1) * page_size
    query = f"""
        SELECT COALESCE(u.first_name, u.username, 'Player') as name, w.{category} as value, w.user_id as uid
        FROM wallets w
        LEFT JOIN users u ON u.id = w.user_id AND u.bot_id = w.bot_id
        WHERE w.bot_id = $1
        ORDER BY w.{category} DESC
        LIMIT $2 OFFSET $3
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, bid, page_size, offset)
        total = await conn.fetchval("SELECT COUNT(*) FROM wallets WHERE bot_id = $1", bid)
    return rows, total


# ---------------------------------------------------------------------------
# XP and levels
# ---------------------------------------------------------------------------

def level_for_xp(xp: int) -> int:
    return 1 + int(math.sqrt(max(0, xp) / config.XP_LEVEL_BASE))


async def award_xp(conn, user_id: int, amount: int, bot_id: int | None = None) -> dict:
    """Call inside an open transaction. Adds XP, applies level-up rewards."""
    bid = bot_context.resolve_bot_id(bot_id)
    row = await conn.fetchrow(
        "UPDATE wallets SET xp = xp + $1, updated_at = now() "
        "WHERE bot_id = $2 AND user_id = $3 RETURNING xp, level",
        amount,
        bid,
        user_id,
    )
    if row is None:
        return {"xp_gained": 0, "leveled_up": False}
    old_level = row["level"]
    new_level = level_for_xp(row["xp"])
    result = {
        "xp_gained": amount,
        "leveled_up": False,
        "new_level": old_level,
        "cash_bonus": 0,
        "gem_bonus": 0,
    }
    if new_level > old_level:
        cash = sum(lv * config.LEVEL_UP_CASH_PER_LEVEL for lv in range(old_level + 1, new_level + 1))
        gems = sum(1 for lv in range(old_level + 1, new_level + 1) if lv % config.LEVEL_UP_GEM_EVERY == 0)
        await conn.execute(
            "UPDATE wallets SET level = $1, cash = cash + $2, gems = gems + $3, updated_at = now() "
            "WHERE bot_id = $4 AND user_id = $5",
            new_level,
            cash,
            gems,
            bid,
            user_id,
        )
        result.update({"leveled_up": True, "new_level": new_level, "cash_bonus": cash, "gem_bonus": gems})
    return result


async def award_rob_xp(conn, user_id: int, amount: int, bot_id: int | None = None) -> dict:
    """Rob XP with an hourly cap. Call inside the rob transaction."""
    bid = bot_context.resolve_bot_id(bot_id)
    await conn.execute(
        "DELETE FROM rob_xp_log WHERE bot_id = $1 AND user_id = $2 AND created_at < now() - interval '1 day'",
        bid,
        user_id,
    )
    used = await conn.fetchval(
        "SELECT COUNT(*) FROM rob_xp_log WHERE bot_id = $1 AND user_id = $2 AND created_at > now() - interval '1 hour'",
        bid,
        user_id,
    )
    if used >= config.ROB_XP_PER_HOUR:
        return {"xp_gained": 0, "leveled_up": False}
    await conn.execute(
        "INSERT INTO rob_xp_log (bot_id, user_id, created_at) VALUES ($1, $2, now())",
        bid,
        user_id,
    )
    return await award_xp(conn, user_id, amount, bot_id=bid)

"""
Economy services for Rumi.

Postgres gives us real transactions with row-level locking (SELECT ... FOR UPDATE),
which is a genuine upgrade over the Cloudflare D1 version — no manual refund-on-
failure logic needed. If anything raises inside a transaction block, Postgres
rolls back everything automatically.
"""

import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

import config

# IST = Indian Standard Time (UTC+5:30). Daily reward resets at IST midnight.
IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------

async def ensure_user_and_wallet(pool: asyncpg.Pool, user_id: int, username: str | None, first_name: str | None) -> None:
    """Creates the user + wallet + streak rows if they don't exist yet. Safe to call every command."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO users (id, username, first_name, created_at, updated_at)
                VALUES ($1, $2, $3, now(), now())
                ON CONFLICT (id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    updated_at = now()
                """,
                user_id, username, first_name,
            )
            await conn.execute(
                """
                INSERT INTO wallets (user_id, cash, gems, xp, level, created_at, updated_at)
                VALUES ($1, 0, 0, 0, 1, now(), now())
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )
            await conn.execute(
                """
                INSERT INTO streaks (user_id, current_streak, longest_streak, last_claim_date, milestones_claimed)
                VALUES ($1, 0, 0, NULL, '[]'::jsonb)
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )


async def get_wallet(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1", user_id)


async def get_wallet_by_username(pool: asyncpg.Pool, username: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT u.id as id, w.*
            FROM users u JOIN wallets w ON w.user_id = u.id
            WHERE u.username ILIKE $1
            """,
            username,
        )

async def get_display_name(pool: asyncpg.Pool, user_id: int) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(first_name, username, 'Player') as name FROM users WHERE id = $1",
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
) -> None:
    """Must be called with a connection that's inside an active transaction,
    alongside the balance-changing statement, so they commit/rollback together."""
    await conn.execute(
        """
        INSERT INTO transactions
        (user_id, type, currency, amount, balance_before, balance_after, related_user_id, source, metadata, idempotency_key, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, now())
        """,
        user_id, type_, currency, amount, balance_before, balance_after,
        related_user_id, source, json.dumps(metadata) if metadata is not None else None, idempotency_key,
    )


# ---------------------------------------------------------------------------
# /tribute — daily reward, resets at UTC midnight
# ---------------------------------------------------------------------------

async def claim_daily(pool: asyncpg.Pool, user_id: int) -> dict:
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                streak = await conn.fetchrow("SELECT * FROM streaks WHERE user_id = $1 FOR UPDATE", user_id)
                wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user_id)
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
                idempotency_key = f"daily:{user_id}:{today.isoformat()}"

                milestones_claimed = json.loads(streak["milestones_claimed"]) if streak else []
                if milestone_hit:
                    milestones_claimed.append(milestone_hit)

                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, gems = gems + $2, updated_at = now() WHERE user_id = $3",
                    cash_reward, gem_reward, user_id,
                )
                await conn.execute(
                    """
                    INSERT INTO streaks (user_id, current_streak, longest_streak, last_claim_date, milestones_claimed)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    ON CONFLICT (user_id) DO UPDATE SET
                        current_streak = EXCLUDED.current_streak,
                        longest_streak = EXCLUDED.longest_streak,
                        last_claim_date = EXCLUDED.last_claim_date,
                        milestones_claimed = EXCLUDED.milestones_claimed
                    """,
                    user_id, new_streak, new_longest, today, json.dumps(milestones_claimed),
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
                )
                xp_info = await award_xp(conn, user_id, config.XP_CLAIM)

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
# /pay — transfers, with real atomic transactions (no manual refund needed)
# ---------------------------------------------------------------------------

async def _transfer_core(pool: asyncpg.Pool, sender_id: int, receiver_id: int, amount: int) -> dict:
    if not isinstance(amount, int) or amount <= 0:
        return {"status": "invalid_amount"}
    if amount < config.MIN_TRANSFER:
        return {"status": "below_minimum"}
    if amount > config.MAX_TRANSFER:
        return {"status": "above_maximum"}
    if sender_id == receiver_id:
        return {"status": "self_transfer"}

    # Lock both wallets in a consistent order (lower id first) so two transfers
    # going in opposite directions can never deadlock against each other.
    first_id, second_id = sorted([sender_id, receiver_id])

    async with pool.acquire() as conn:
        async with conn.transaction():
            first_wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", first_id)
            second_wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", second_id)

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
                "UPDATE wallets SET cash = cash - $1, daily_transferred = $2, daily_transferred_date = $3, updated_at = now() WHERE user_id = $4",
                amount, already_sent + amount, today, sender_id,
            )
            await conn.execute(
                "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id = $2",
                net, receiver_id,
            )

            request_id = str(uuid.uuid4())
            await insert_transaction(
                conn, user_id=sender_id, type_="TRANSFER_SENT", currency="cash",
                amount=-amount, balance_before=sender["cash"], balance_after=sender["cash"] - amount,
                related_user_id=receiver_id, source="pay_command", idempotency_key=f"pay:{request_id}:sent",
                metadata={"tax": tax, "net": net},
            )
            await insert_transaction(
                conn, user_id=receiver_id, type_="TRANSFER_RECEIVED", currency="cash",
                amount=net, balance_before=receiver["cash"], balance_after=receiver["cash"] + net,
                related_user_id=sender_id, source="pay_command", idempotency_key=f"pay:{request_id}:received",
                metadata={"tax": tax, "net": net},
            )

            return {"status": "ok", "new_sender_balance": sender["cash"] - amount, "tax": tax, "received": net}


async def pay(pool: asyncpg.Pool, sender_id: int, receiver_username: str, amount: int) -> dict:
    """/pay @username amount"""
    receiver = await get_wallet_by_username(pool, receiver_username)
    if receiver is None:
        return {"status": "receiver_not_found"}
    return await _transfer_core(pool, sender_id, receiver["id"], amount)


async def pay_to_id(pool: asyncpg.Pool, sender_id: int, receiver_id: int, amount: int) -> dict:
    """/pay 123456789 amount, or replying to someone's message with /pay amount"""
    receiver = await get_wallet(pool, receiver_id)
    if receiver is None:
        return {"status": "receiver_not_found"}
    return await _transfer_core(pool, sender_id, receiver_id, amount)


# ---------------------------------------------------------------------------
# /ward — shield against robbery
# ---------------------------------------------------------------------------

async def apply_ward(pool: asyncpg.Pool, user_id: int) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user_id)
            if wallet is None:
                return {"status": "error", "message": "Wallet missing"}
            now = datetime.now(timezone.utc)
            if wallet["shield_until"] and wallet["shield_until"] > now:
                return {"status": "already_shielded", "shield_until": wallet["shield_until"]}

            if wallet["cash"] < config.SHIELD_COST:
                return {"status": "insufficient_funds", "required": config.SHIELD_COST, "available": wallet["cash"]}

            shield_until = now + timedelta(hours=config.SHIELD_DURATION_HOURS)
            await conn.execute(
                "UPDATE wallets SET cash = cash - $1, shield_until = $2, updated_at = now() WHERE user_id = $3",
                config.SHIELD_COST, shield_until, user_id,
            )
            await insert_transaction(
                conn, user_id=user_id, type_="SHOP_PURCHASE", currency="cash",
                amount=-config.SHIELD_COST, balance_before=wallet["cash"], balance_after=wallet["cash"] - config.SHIELD_COST,
                source="ward_command",
            )
            return {"status": "ok", "shield_until": shield_until}

# ---------------------------------------------------------------------------
# /rob — PvP looting, protected by cooldown + shield checks
# ---------------------------------------------------------------------------

async def rob(pool: asyncpg.Pool, robber_id: int, victim_id: int, amount: int) -> dict:
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
            # Lock the robber's cooldown row first — serializes concurrent rob
            # attempts by the same robber so the cooldown can't be double-spent.
            cooldown_row = await conn.fetchrow(
                "SELECT last_rob_at FROM rob_cooldowns WHERE user_id = $1 FOR UPDATE", robber_id
            )
            if cooldown_row and cooldown_row["last_rob_at"]:
                cooldown_delta = timedelta(minutes=config.ROB_COOLDOWN_MINUTES)
                elapsed = now - cooldown_row["last_rob_at"]
                if elapsed < cooldown_delta:
                    return {"status": "cooldown", "retry_at": cooldown_row["last_rob_at"] + cooldown_delta}

            first_wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", first_id)
            second_wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", second_id)
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
            max_allowed = min(int(victim["cash"] * config.ROB_STEAL_PERCENT_MAX), config.ROB_MAX_AMOUNT)
            if amount > max_allowed:
                return {"status": "amount_too_high", "max_allowed": max_allowed}

            success = random.random() < config.ROB_SUCCESS_CHANCE

            if success:
                stolen = amount
                tax = int(stolen * config.ROB_TAX_PERCENT)
                net = stolen - tax
                await conn.execute("UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id = $2", stolen, victim_id)
                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, wins = wins + 1, updated_at = now() WHERE user_id = $2",
                    net, robber_id,
                )
                await insert_transaction(
                    conn, user_id=robber_id, type_="ROB_SUCCESS", currency="cash",
                    amount=net, balance_before=robber["cash"], balance_after=robber["cash"] + net,
                    metadata={"tax": tax, "gross": stolen},
                    related_user_id=victim_id, source="rob_command",
                )
                await insert_transaction(
                    conn, user_id=victim_id, type_="ROB_SUCCESS", currency="cash",
                    amount=-stolen, balance_before=victim["cash"], balance_after=victim["cash"] - stolen,
                    related_user_id=robber_id, source="rob_command",
                )
                result = {"status": "success", "amount": stolen, "tax": tax, "net": net}
                result["xp"] = await award_rob_xp(conn, robber_id, config.XP_ROB_SUCCESS)
            else:
                percent = random.uniform(config.ROB_FAIL_PENALTY_PERCENT_MIN, config.ROB_FAIL_PENALTY_PERCENT_MAX)
                penalty = min(robber["cash"], max(1, int(amount * percent)))
                await conn.execute("UPDATE wallets SET losses = losses + 1, updated_at = now() WHERE user_id = $1", robber_id)
                if penalty > 0:
                    await conn.execute("UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id = $2", penalty, robber_id)
                    await conn.execute("UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id = $2", penalty, victim_id)
                    await insert_transaction(
                        conn, user_id=robber_id, type_="ROB_FAILED", currency="cash",
                        amount=-penalty, balance_before=robber["cash"], balance_after=robber["cash"] - penalty,
                        related_user_id=victim_id, source="rob_command",
                    )
                    await insert_transaction(
                        conn, user_id=victim_id, type_="ROB_FAILED", currency="cash",
                        amount=penalty, balance_before=victim["cash"], balance_after=victim["cash"] + penalty,
                        related_user_id=robber_id, source="rob_command",
                    )
                result = {"status": "failed", "penalty": penalty}
                result["xp"] = await award_rob_xp(conn, robber_id, config.XP_ROB_FAIL)

            await conn.execute(
                """
                INSERT INTO rob_cooldowns (user_id, last_rob_at) VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET last_rob_at = EXCLUDED.last_rob_at
                """,
                robber_id, now,
            )
            return result


# ---------------------------------------------------------------------------
# /empire — leaderboard
# ---------------------------------------------------------------------------

async def get_leaderboard(pool: asyncpg.Pool, category: str, page: int, page_size: int):
    if category not in ("cash", "xp", "gems"):
        raise ValueError("Invalid leaderboard category")  # never interpolate raw user input into SQL
    offset = (page - 1) * page_size
    query = f"""
        SELECT COALESCE(u.first_name, u.username, 'Player') as name, w.{category} as value
        FROM wallets w JOIN users u ON u.id = w.user_id
        ORDER BY w.{category} DESC
        LIMIT $1 OFFSET $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, page_size, offset)
        total = await conn.fetchval("SELECT COUNT(*) FROM wallets")
    return rows, total


# ---------------------------------------------------------------------------
# XP and levels
# ---------------------------------------------------------------------------
import math as _math


def level_for_xp(xp: int) -> int:
    return 1 + int(_math.sqrt(max(0, xp) / config.XP_LEVEL_BASE))


async def award_xp(conn, user_id: int, amount: int) -> dict:
    """Call inside an open transaction. Adds XP, applies level-up rewards."""
    row = await conn.fetchrow(
        "UPDATE wallets SET xp = xp + $1, updated_at = now() WHERE user_id = $2 RETURNING xp, level",
        amount, user_id,
    )
    if row is None:
        return {"xp_gained": 0, "leveled_up": False}
    old_level = row["level"]
    new_level = level_for_xp(row["xp"])
    result = {"xp_gained": amount, "leveled_up": False, "new_level": old_level,
              "cash_bonus": 0, "gem_bonus": 0}
    if new_level > old_level:
        cash = sum(lv * config.LEVEL_UP_CASH_PER_LEVEL for lv in range(old_level + 1, new_level + 1))
        gems = sum(1 for lv in range(old_level + 1, new_level + 1) if lv % config.LEVEL_UP_GEM_EVERY == 0)
        await conn.execute(
            "UPDATE wallets SET level = $1, cash = cash + $2, gems = gems + $3, updated_at = now() WHERE user_id = $4",
            new_level, cash, gems, user_id,
        )
        result.update({"leveled_up": True, "new_level": new_level, "cash_bonus": cash, "gem_bonus": gems})
    return result



async def award_rob_xp(conn, user_id: int, amount: int) -> dict:
    """Rob XP with an hourly cap. Call inside the rob transaction."""
    await conn.execute(
        "DELETE FROM rob_xp_log WHERE user_id = $1 AND created_at < now() - interval '1 day'", user_id
    )
    used = await conn.fetchval(
        "SELECT COUNT(*) FROM rob_xp_log WHERE user_id = $1 AND created_at > now() - interval '1 hour'",
        user_id,
    )
    if used >= config.ROB_XP_PER_HOUR:
        return {"xp_gained": 0, "leveled_up": False}
    await conn.execute("INSERT INTO rob_xp_log (user_id, created_at) VALUES ($1, now())", user_id)
    return await award_xp(conn, user_id, amount)

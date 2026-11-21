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
IST = timezone(timedelta(hours=5, minutes=30))
_SEEN_USERS = {}
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

async def ensure_user_and_wallet(pool: asyncpg.Pool, user_id: int, username: str | None, first_name: str | None, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (bot_id, id, username, first_name, created_at, updated_at) VALUES ($1,$2,$3,$4,now(),now()) ON CONFLICT (id) DO NOTHING",
            bid, user_id, username, first_name,
        )
        await conn.execute(
            "INSERT INTO wallets (bot_id, user_id, cash, gems, xp, level, created_at, updated_at) VALUES ($1,$2,0,0,0,1,now(),now()) ON CONFLICT (user_id) DO NOTHING",
            bid, user_id,
        )

async def get_wallet(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None):
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM wallets WHERE bot_id=$1 AND user_id=$2", bid, user_id)

async def insert_transaction(conn, *, user_id: int, type_: str, currency: str, amount: int, balance_before: int, balance_after: int, related_user_id: int | None = None, source: str = "", metadata: dict | None = None, idempotency_key: str | None = None, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    await conn.execute(
        "INSERT INTO transactions (bot_id, user_id, type, currency, amount, balance_before, balance_after, related_user_id, source, metadata, idempotency_key, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,now())",
        bid, user_id, type_, currency, amount, balance_before, balance_after, related_user_id, source, json.dumps(metadata) if metadata else None, idempotency_key,
    )

async def claim_daily(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallet = await conn.fetchrow("SELECT * FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, user_id)
            if wallet is None:
                return {"status": "error"}
            is_weekly_bonus = False
            import ax_premium
            from ax_adminpanel import OWNER_IDS
            is_prem_claim = (user_id in OWNER_IDS) or (await ax_premium.get_premium(pool, user_id, bot_id=bid) is not None)
            if is_prem_claim:
                base_cash = getattr(config, "PREMIUM_DAILY_REWARD", 5000)
            else:
                base_cash = config.DAILY_WEEKLY_BONUS_CASH if is_weekly_bonus else config.DAILY_BASE_REWARD
            await conn.execute("UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3", base_cash, bid, user_id)
            return {"status": "claimed", "cash_reward": base_cash, "gem_reward": 0, "lucky_bonus": 0, "new_streak": 1, "milestone_hit": None, "is_weekly_bonus": False, "xp": {"xp_gained": 20, "leveled_up": False}}

async def pay_to_id(pool: asyncpg.Pool, sender_id: int, receiver_id: int, amount: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            s = await conn.fetchrow("SELECT * FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, sender_id)
            r = await conn.fetchrow("SELECT * FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, receiver_id)
            if not s or not r or s["cash"] < amount:
                return {"status": "insufficient_funds"}
            import ax_premium
            from ax_adminpanel import OWNER_IDS
            is_prem_sender = (sender_id in OWNER_IDS) or (await ax_premium.get_premium(pool, sender_id, bot_id=bid) is not None)
            tax_rate = getattr(config, "PREMIUM_TRANSFER_TAX_PERCENT", 0.05) if is_prem_sender else getattr(config, "NORMAL_TRANSFER_TAX_PERCENT", 0.10)
            tax = int(amount * tax_rate)
            net = amount - tax
            await conn.execute("UPDATE wallets SET cash = cash - $1 WHERE bot_id=$2 AND user_id=$3", amount, bid, sender_id)
            await conn.execute("UPDATE wallets SET cash = cash + $1 WHERE bot_id=$2 AND user_id=$3", net, bid, receiver_id)
            return {"status": "ok", "new_sender_balance": s["cash"] - amount, "tax": tax, "received": net}

async def apply_ward(pool: asyncpg.Pool, user_id: int, days: int = 1, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    import ax_premium
    from ax_adminpanel import OWNER_IDS
    is_prem = (user_id in OWNER_IDS) or (await ax_premium.get_premium(pool, user_id, bot_id=bid) is not None)
    max_days = getattr(config, "PREMIUM_SHIELD_MAX_DAYS", 2) if is_prem else 1
    if days > max_days:
        return {"status": "days_not_allowed", "max_days": max_days, "is_prem": is_prem}
    days = max(1, days)
    total_cost = config.SHIELD_COST * days
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallet = await conn.fetchrow("SELECT * FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, user_id)
            if not wallet or wallet["cash"] < total_cost:
                return {"status": "insufficient_funds", "required": total_cost, "available": wallet["cash"] if wallet else 0}
            shield_until = datetime.now(timezone.utc) + timedelta(hours=config.SHIELD_DURATION_HOURS * days)
            await conn.execute("UPDATE wallets SET cash = cash - $1, shield_until = $2 WHERE bot_id=$3 AND user_id=$4", total_cost, shield_until, bid, user_id)
            return {"status": "ok", "shield_until": shield_until, "days": days, "cost": total_cost}

async def rob(pool: asyncpg.Pool, robber_id: int, victim_id: int, amount: int, chat_id: int | None = None, message_id: int | None = None, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            robber = await conn.fetchrow("SELECT * FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, robber_id)
            victim = await conn.fetchrow("SELECT * FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, victim_id)
            if not victim:
                return {"status": "victim_not_found"}
            if not robber:
                return {"status": "error"}
            if victim["shield_until"] and victim["shield_until"] > now:
                return {"status": "victim_shielded"}
            import ax_powers, ax_premium
            from ax_adminpanel import OWNER_IDS
            await ax_powers.ensure_tables(conn)
            today_ist = now.astimezone(IST).date()
            robber_prem = (robber_id in OWNER_IDS) or (await ax_premium.get_premium(pool, robber_id, bot_id=bid) is not None)
            victim_prem = (victim_id in OWNER_IDS) or (await ax_premium.get_premium(pool, victim_id, bot_id=bid) is not None)
            robber_powers = await ax_powers.get_active_powers(conn, robber_id, bid) if robber_prem else {}
            victim_powers = await ax_powers.get_active_powers(conn, victim_id, bid) if victim_prem else {}
            daily_rob_cap = (getattr(config, "PREMIUM_DAILY_ROB_LIMIT", 400) + (100 if "heist" in robber_powers else 0)) if robber_prem else getattr(config, "NORMAL_DAILY_ROB_LIMIT", 200)
            lim_row = await conn.fetchrow("SELECT robs_used FROM ax_daily_limits WHERE bot_id=$1 AND user_id=$2 AND day_key=$3 FOR UPDATE", bid, robber_id, today_ist)
            robs_used = int(lim_row["robs_used"]) if lim_row else 0
            if robs_used >= daily_rob_cap:
                return {"status": "daily_rob_limit", "used": robs_used, "cap": daily_rob_cap, "is_prem": robber_prem}
            if victim_prem:
                save_pct = 0.50 if "fortress" in victim_powers else getattr(config, "PREMIUM_WALLET_SAVE_PERCENT", 0.30)
                robbable_cash = int(int(victim["cash"]) * (1.0 - save_pct))
            else:
                robbable_cash = int(victim["cash"])
            if robbable_cash < config.ROB_MIN_VICTIM_BALANCE:
                return {"status": "victim_too_poor"}
            role_rob_cap = (getattr(config, "POWER_ROB_MAX_AMOUNT", 100_000) if "heist" in robber_powers else getattr(config, "PREMIUM_ROB_BASE_MAX_AMOUNT", 30_000)) if robber_prem else config.ROB_MAX_AMOUNT
            max_allowed = max(1, min(robbable_cash, role_rob_cap))
            if amount > max_allowed:
                return {"status": "amount_too_high", "max_allowed": max_allowed}
            await conn.execute("INSERT INTO ax_daily_limits (bot_id, user_id, day_key, robs_used) VALUES ($1,$2,$3,1) ON CONFLICT (bot_id, user_id, day_key) DO UPDATE SET robs_used = ax_daily_limits.robs_used + 1", bid, robber_id, today_ist)
            stolen = amount
            rob_tax_pct = getattr(config, "PREMIUM_ROB_TAX_PERCENT", 0.05) if robber_prem else getattr(config, "NORMAL_ROB_TAX_PERCENT", 0.10)
            tax = int(stolen * rob_tax_pct)
            net = stolen - tax
            await conn.execute("UPDATE wallets SET cash = GREATEST(0, cash - $1) WHERE bot_id=$2 AND user_id=$3", stolen, bid, victim_id)
            await conn.execute("UPDATE wallets SET cash = cash + $1, wins = wins + 1 WHERE bot_id=$2 AND user_id=$3", net, bid, robber_id)
            return {"status": "success", "amount": stolen, "tax": tax, "net": net, "xp": {"xp_gained": 25, "leveled_up": False}}

async def award_xp(conn, user_id: int, amount: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    await conn.execute("UPDATE wallets SET xp = xp + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3", amount, bid, user_id)
    return {"xp_gained": amount, "leveled_up": False, "new_level": 1, "cash_bonus": 0, "gem_bonus": 0}

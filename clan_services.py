"""Clan services: every coin move is locked and ledgered (bot_id aware)."""
import json
import re

import asyncpg

import bot_context
import config

NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


async def _ledger(
    conn,
    uid: int,
    type_: str,
    amount: int,
    before: int,
    after: int,
    meta: dict,
    bot_id: int,
) -> None:
    await conn.execute(
        "INSERT INTO transactions (bot_id, user_id, type, currency, amount, balance_before, "
        "balance_after, source, metadata) VALUES ($1, $2, $3, 'cash', $4, $5, $6, 'clan', $7::jsonb)",
        bot_id,
        uid,
        type_,
        amount,
        before,
        after,
        json.dumps(meta),
    )


async def get_membership(conn, uid: int, bot_id: int | None = None):
    bid = bot_context.resolve_bot_id(bot_id)
    return await conn.fetchrow(
        "SELECT c.*, m.role FROM clan_members m "
        "JOIN clans c ON c.id = m.clan_id AND c.bot_id = m.bot_id "
        "WHERE m.bot_id = $1 AND m.user_id = $2",
        bid,
        uid,
    )


async def create_clan(pool: asyncpg.Pool, uid: int, name: str, bot_id: int | None = None) -> dict:
    if not (config.CLAN_NAME_MIN <= len(name) <= config.CLAN_NAME_MAX) or not NAME_RE.match(name):
        return {"status": "bad_name"}
    bid = bot_context.resolve_bot_id(bot_id)
    fee = config.CLAN_CREATE_FEE
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                w = await conn.fetchrow(
                    "SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                    bid,
                    uid,
                )
                if w is None:
                    return {"status": "error"}
                if await conn.fetchval(
                    "SELECT 1 FROM clan_members WHERE bot_id=$1 AND user_id=$2",
                    bid,
                    uid,
                ):
                    return {"status": "already_in_clan"}
                if await conn.fetchval(
                    "SELECT 1 FROM clans WHERE bot_id=$1 AND LOWER(name)=LOWER($2)",
                    bid,
                    name,
                ):
                    return {"status": "name_taken"}
                if w["cash"] < fee:
                    return {"status": "poor", "need": fee, "have": w["cash"]}
                cid = await conn.fetchval(
                    "INSERT INTO clans (bot_id, name, leader_id) VALUES ($1, $2, $3) RETURNING id",
                    bid,
                    name,
                    uid,
                )
                await conn.execute(
                    "INSERT INTO clan_members (bot_id, user_id, clan_id, role) VALUES ($1, $2, $3, 'leader')",
                    bid,
                    uid,
                    cid,
                )
                await conn.execute(
                    "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                    fee,
                    bid,
                    uid,
                )
                await _ledger(
                    conn,
                    uid,
                    "CLAN_CREATE",
                    -fee,
                    w["cash"],
                    w["cash"] - fee,
                    {"clan_id": cid, "name": name},
                    bid,
                )
                return {"status": "ok", "name": name, "fee": fee}
    except asyncpg.UniqueViolationError as exc:
        cname = getattr(exc, "constraint_name", "") or ""
        if "clan_members" in cname:
            return {"status": "already_in_clan"}
        return {"status": "name_taken"}


async def deposit(pool: asyncpg.Pool, uid: int, amount: int, bot_id: int | None = None) -> dict:
    if not isinstance(amount, int) or amount <= 0:
        return {"status": "bad_amount"}
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            w = await conn.fetchrow(
                "SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bid,
                uid,
            )
            m = await conn.fetchrow(
                "SELECT clan_id FROM clan_members WHERE bot_id=$1 AND user_id=$2",
                bid,
                uid,
            )
            if m is None:
                return {"status": "no_clan"}
            if w is None or w["cash"] < amount:
                return {"status": "poor"}
            c = await conn.fetchrow(
                "SELECT level, vault_balance FROM clans WHERE bot_id=$1 AND id=$2 FOR UPDATE",
                bid,
                m["clan_id"],
            )
            if c is None:
                return {"status": "no_clan"}
            vault, level = c["vault_balance"] + amount, c["level"]
            for lv, need in sorted(config.CLAN_LEVEL_REQUIREMENTS.items()):
                if vault >= need and lv > level and lv <= config.CLAN_MAX_LEVEL:
                    level = lv
            await conn.execute(
                "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                amount,
                bid,
                uid,
            )
            await conn.execute(
                "UPDATE clans SET vault_balance = $1, level = $2 WHERE bot_id=$3 AND id=$4",
                vault,
                level,
                bid,
                m["clan_id"],
            )
            await _ledger(
                conn,
                uid,
                "CLAN_DEPOSIT",
                -amount,
                w["cash"],
                w["cash"] - amount,
                {"clan_id": m["clan_id"]},
                bid,
            )
            return {
                "status": "ok",
                "vault": vault,
                "level": level,
                "leveled_up": level > c["level"],
            }


async def join_clan(pool: asyncpg.Pool, uid: int, clan_id: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                c = await conn.fetchrow(
                    "SELECT id, name FROM clans WHERE bot_id=$1 AND id=$2 FOR UPDATE",
                    bid,
                    clan_id,
                )
                if c is None:
                    return {"status": "gone"}
                n = await conn.fetchval(
                    "SELECT COUNT(*) FROM clan_members WHERE bot_id=$1 AND clan_id=$2",
                    bid,
                    clan_id,
                )
                if n >= config.CLAN_MAX_MEMBERS:
                    return {"status": "full"}
                await conn.execute(
                    "INSERT INTO clan_members (bot_id, user_id, clan_id) VALUES ($1, $2, $3)",
                    bid,
                    uid,
                    clan_id,
                )
                return {"status": "ok", "name": c["name"]}
    except asyncpg.UniqueViolationError:
        return {"status": "already_in_clan"}


async def get_info(pool: asyncpg.Pool, uid: int, bot_id: int | None = None) -> dict | None:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        c = await get_membership(conn, uid, bot_id=bid)
        if c is None:
            return None
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM clan_members WHERE bot_id=$1 AND clan_id=$2",
            bid,
            c["id"],
        )
        leader = await conn.fetchval(
            "SELECT COALESCE(first_name, username, 'Leader') FROM users WHERE bot_id=$1 AND id=$2",
            bid,
            c["leader_id"],
        )
        return {"clan": c, "members": n, "leader": leader or "Leader"}

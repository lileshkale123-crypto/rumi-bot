"""Clan services: every coin move is locked and ledgered."""
import json
import re

import asyncpg

import config

NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


async def _ledger(conn, uid, type_, amount, before, after, meta):
    await conn.execute(
        "INSERT INTO transactions (user_id, type, currency, amount, balance_before, "
        "balance_after, source, metadata) VALUES ($1, $2, 'cash', $3, $4, $5, 'clan', $6::jsonb)",
        uid, type_, amount, before, after, json.dumps(meta),
    )


async def get_membership(conn, uid):
    return await conn.fetchrow(
        "SELECT c.*, m.role FROM clan_members m JOIN clans c ON c.id = m.clan_id "
        "WHERE m.user_id = $1", uid)


async def create_clan(pool, uid, name):
    if not (config.CLAN_NAME_MIN <= len(name) <= config.CLAN_NAME_MAX) or not NAME_RE.match(name):
        return {"status": "bad_name"}
    fee = config.CLAN_CREATE_FEE
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                w = await conn.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
                if w is None:
                    return {"status": "error"}
                if await conn.fetchval("SELECT 1 FROM clan_members WHERE user_id=$1", uid):
                    return {"status": "already_in_clan"}
                if w["cash"] < fee:
                    return {"status": "poor", "need": fee, "have": w["cash"]}
                cid = await conn.fetchval(
                    "INSERT INTO clans (name, leader_id) VALUES ($1, $2) RETURNING id", name, uid)
                await conn.execute(
                    "INSERT INTO clan_members (user_id, clan_id, role) VALUES ($1, $2, 'leader')", uid, cid)
                await conn.execute(
                    "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id=$2", fee, uid)
                await _ledger(conn, uid, "CLAN_CREATE", -fee, w["cash"], w["cash"] - fee,
                              {"clan_id": cid, "name": name})
                return {"status": "ok", "name": name, "fee": fee}
    except asyncpg.UniqueViolationError:
        return {"status": "name_taken"}


async def deposit(pool, uid, amount):
    if amount <= 0:
        return {"status": "bad_amount"}
    async with pool.acquire() as conn:
        async with conn.transaction():
            w = await conn.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
            m = await conn.fetchrow("SELECT clan_id FROM clan_members WHERE user_id=$1", uid)
            if m is None:
                return {"status": "no_clan"}
            if w is None or w["cash"] < amount:
                return {"status": "poor"}
            c = await conn.fetchrow(
                "SELECT level, vault_balance FROM clans WHERE id=$1 FOR UPDATE", m["clan_id"])
            if c is None:
                return {"status": "no_clan"}
            vault, level = c["vault_balance"] + amount, c["level"]
            for lv, need in sorted(config.CLAN_LEVEL_REQUIREMENTS.items()):
                if vault >= need and lv > level and lv <= config.CLAN_MAX_LEVEL:
                    level = lv
            await conn.execute(
                "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id=$2", amount, uid)
            await conn.execute(
                "UPDATE clans SET vault_balance = $1, level = $2 WHERE id=$3", vault, level, m["clan_id"])
            await _ledger(conn, uid, "CLAN_DEPOSIT", -amount, w["cash"], w["cash"] - amount,
                          {"clan_id": m["clan_id"]})
            return {"status": "ok", "vault": vault, "level": level, "leveled_up": level > c["level"]}


async def join_clan(pool, uid, clan_id):
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                c = await conn.fetchrow("SELECT id, name FROM clans WHERE id=$1 FOR UPDATE", clan_id)
                if c is None:
                    return {"status": "gone"}
                n = await conn.fetchval("SELECT COUNT(*) FROM clan_members WHERE clan_id=$1", clan_id)
                if n >= config.CLAN_MAX_MEMBERS:
                    return {"status": "full"}
                await conn.execute(
                    "INSERT INTO clan_members (user_id, clan_id) VALUES ($1, $2)", uid, clan_id)
                return {"status": "ok", "name": c["name"]}
    except asyncpg.UniqueViolationError:
        return {"status": "already_in_clan"}


async def get_info(pool, uid):
    async with pool.acquire() as conn:
        c = await get_membership(conn, uid)
        if c is None:
            return None
        n = await conn.fetchval("SELECT COUNT(*) FROM clan_members WHERE clan_id=$1", c["id"])
        leader = await conn.fetchval(
            "SELECT COALESCE(first_name, username, 'Leader') FROM users WHERE id=$1", c["leader_id"])
        return {"clan": c, "members": n, "leader": leader}

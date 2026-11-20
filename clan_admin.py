"""Clan admin services: leave, kick, leader change, disband, leaderboard (bot_id aware)."""
import asyncpg

import bot_context
from clan_services import _ledger


async def _lock_as_leader(conn, uid: int, bot_id: int) -> int | None:
    """Returns clan_id if uid is the leader (clan row locked), else None."""
    m = await conn.fetchrow(
        "SELECT clan_id FROM clan_members WHERE bot_id=$1 AND user_id=$2 AND role='leader'",
        bot_id,
        uid,
    )
    if m is None:
        return None
    lid = await conn.fetchval(
        "SELECT leader_id FROM clans WHERE bot_id=$1 AND id=$2 FOR UPDATE",
        bot_id,
        m["clan_id"],
    )
    return m["clan_id"] if lid == uid else None


async def leave_clan(pool: asyncpg.Pool, uid: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow(
                "SELECT clan_id FROM clan_members WHERE bot_id=$1 AND user_id=$2",
                bid,
                uid,
            )
            if m is None:
                return {"status": "no_clan"}
            await conn.fetchval(
                "SELECT id FROM clans WHERE bot_id=$1 AND id=$2 FOR UPDATE",
                bid,
                m["clan_id"],
            )
            r = await conn.execute(
                "DELETE FROM clan_members WHERE bot_id=$1 AND user_id=$2 AND role='member'",
                bid,
                uid,
            )
            return {"status": "ok"} if r.endswith("1") else {"status": "leader_cannot_leave"}


async def kick_member(
    pool: asyncpg.Pool,
    leader_id: int,
    target_id: int,
    bot_id: int | None = None,
) -> dict:
    if leader_id == target_id:
        return {"status": "self"}
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            cid = await _lock_as_leader(conn, leader_id, bid)
            if cid is None:
                return {"status": "not_leader"}
            r = await conn.execute(
                "DELETE FROM clan_members WHERE bot_id=$1 AND user_id=$2 AND clan_id=$3 AND role='member'",
                bid,
                target_id,
                cid,
            )
            return {"status": "ok"} if r.endswith("1") else {"status": "not_in_clan"}


async def transfer_leader(
    pool: asyncpg.Pool,
    leader_id: int,
    target_id: int,
    bot_id: int | None = None,
) -> dict:
    if leader_id == target_id:
        return {"status": "self"}
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            cid = await _lock_as_leader(conn, leader_id, bid)
            if cid is None:
                return {"status": "not_leader"}
            if not await conn.fetchval(
                "SELECT 1 FROM clan_members WHERE bot_id=$1 AND user_id=$2 AND clan_id=$3",
                bid,
                target_id,
                cid,
            ):
                return {"status": "not_in_clan"}
            await conn.execute(
                "UPDATE clan_members SET role='member' WHERE bot_id=$1 AND user_id=$2",
                bid,
                leader_id,
            )
            await conn.execute(
                "UPDATE clan_members SET role='leader' WHERE bot_id=$1 AND user_id=$2",
                bid,
                target_id,
            )
            await conn.execute(
                "UPDATE clans SET leader_id=$1 WHERE bot_id=$2 AND id=$3",
                target_id,
                bid,
                cid,
            )
            return {"status": "ok"}


async def disband(pool: asyncpg.Pool, uid: int, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow(
                "SELECT clan_id FROM clan_members WHERE bot_id=$1 AND user_id=$2 AND role='leader'",
                bid,
                uid,
            )
            if m is None:
                return {"status": "not_leader"}
            cid = m["clan_id"]
            q = "SELECT user_id FROM clan_members WHERE bot_id=$1 AND clan_id=$2 ORDER BY user_id"
            ids = [r["user_id"] for r in await conn.fetch(q, bid, cid)]
            if not ids:
                return {"status": "retry"}
            wallets = {
                r["user_id"]: r["cash"]
                for r in await conn.fetch(
                    "SELECT user_id, cash FROM wallets WHERE bot_id=$1 AND user_id = ANY($2::bigint[]) "
                    "ORDER BY user_id FOR UPDATE",
                    bid,
                    ids,
                )
            }
            c = await conn.fetchrow(
                "SELECT leader_id, vault_balance, name FROM clans WHERE bot_id=$1 AND id=$2 FOR UPDATE",
                bid,
                cid,
            )
            again = [r["user_id"] for r in await conn.fetch(q, bid, cid)]
            if c is None or c["leader_id"] != uid or again != ids:
                return {"status": "retry"}
            share, extra = divmod(c["vault_balance"], len(ids))
            for u in ids:
                pay = share + (extra if u == uid else 0)
                if pay and u in wallets:
                    await conn.execute(
                        "UPDATE wallets SET cash = cash + $1, updated_at = now() "
                        "WHERE bot_id=$2 AND user_id=$3",
                        pay,
                        bid,
                        u,
                    )
                    await _ledger(
                        conn,
                        u,
                        "CLAN_DISBAND_PAYOUT",
                        pay,
                        wallets[u],
                        wallets[u] + pay,
                        {"clan": c["name"]},
                        bid,
                    )
            await conn.execute("DELETE FROM clans WHERE bot_id=$1 AND id=$2", bid, cid)
            return {
                "status": "ok",
                "name": c["name"],
                "vault": c["vault_balance"],
                "share": share,
                "members": len(ids),
            }


async def top_clans(pool: asyncpg.Pool, bot_id: int | None = None):
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT c.name, c.level, c.vault_balance, "
            "(SELECT COUNT(*) FROM clan_members m WHERE m.bot_id = c.bot_id AND m.clan_id = c.id) AS members "
            "FROM clans c WHERE c.bot_id = $1 ORDER BY c.vault_balance DESC, c.id LIMIT 10",
            bid,
        )

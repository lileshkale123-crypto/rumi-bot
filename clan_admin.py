"""Clan admin services: leave, kick, leader change, disband, leaderboard."""
from clan_services import _ledger


async def _lock_as_leader(conn, uid):
    """Returns clan_id if uid is the leader (clan row locked), else None."""
    m = await conn.fetchrow(
        "SELECT clan_id FROM clan_members WHERE user_id=$1 AND role='leader'", uid)
    if m is None:
        return None
    lid = await conn.fetchval("SELECT leader_id FROM clans WHERE id=$1 FOR UPDATE", m["clan_id"])
    return m["clan_id"] if lid == uid else None


async def leave_clan(pool, uid):
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow("SELECT clan_id FROM clan_members WHERE user_id=$1", uid)
            if m is None:
                return {"status": "no_clan"}
            await conn.fetchval("SELECT id FROM clans WHERE id=$1 FOR UPDATE", m["clan_id"])
            r = await conn.execute(
                "DELETE FROM clan_members WHERE user_id=$1 AND role='member'", uid)
            return {"status": "ok"} if r.endswith("1") else {"status": "leader_cannot_leave"}


async def kick_member(pool, leader_id, target_id):
    if leader_id == target_id:
        return {"status": "self"}
    async with pool.acquire() as conn:
        async with conn.transaction():
            cid = await _lock_as_leader(conn, leader_id)
            if cid is None:
                return {"status": "not_leader"}
            r = await conn.execute(
                "DELETE FROM clan_members WHERE user_id=$1 AND clan_id=$2 AND role='member'",
                target_id, cid)
            return {"status": "ok"} if r.endswith("1") else {"status": "not_in_clan"}


async def transfer_leader(pool, leader_id, target_id):
    if leader_id == target_id:
        return {"status": "self"}
    async with pool.acquire() as conn:
        async with conn.transaction():
            cid = await _lock_as_leader(conn, leader_id)
            if cid is None:
                return {"status": "not_leader"}
            if not await conn.fetchval(
                    "SELECT 1 FROM clan_members WHERE user_id=$1 AND clan_id=$2", target_id, cid):
                return {"status": "not_in_clan"}
            await conn.execute("UPDATE clan_members SET role='member' WHERE user_id=$1", leader_id)
            await conn.execute("UPDATE clan_members SET role='leader' WHERE user_id=$1", target_id)
            await conn.execute("UPDATE clans SET leader_id=$1 WHERE id=$2", target_id, cid)
            return {"status": "ok"}


async def disband(pool, uid):
    async with pool.acquire() as conn:
        async with conn.transaction():
            m = await conn.fetchrow(
                "SELECT clan_id FROM clan_members WHERE user_id=$1 AND role='leader'", uid)
            if m is None:
                return {"status": "not_leader"}
            cid = m["clan_id"]
            q = "SELECT user_id FROM clan_members WHERE clan_id=$1 ORDER BY user_id"
            ids = [r["user_id"] for r in await conn.fetch(q, cid)]
            # wallets first, clan second: same order as deposit, so no deadlock
            wallets = {r["user_id"]: r["cash"] for r in await conn.fetch(
                "SELECT user_id, cash FROM wallets WHERE user_id = ANY($1::bigint[]) "
                "ORDER BY user_id FOR UPDATE", ids)}
            c = await conn.fetchrow(
                "SELECT leader_id, vault_balance, name FROM clans WHERE id=$1 FOR UPDATE", cid)
            again = [r["user_id"] for r in await conn.fetch(q, cid)]
            if c is None or c["leader_id"] != uid or again != ids:
                return {"status": "retry"}
            share, extra = divmod(c["vault_balance"], len(ids))
            for u in ids:
                pay = share + (extra if u == uid else 0)
                if pay and u in wallets:
                    await conn.execute(
                        "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id=$2",
                        pay, u)
                    await _ledger(conn, u, "CLAN_DISBAND_PAYOUT", pay, wallets[u],
                                  wallets[u] + pay, {"clan": c["name"]})
            await conn.execute("DELETE FROM clans WHERE id=$1", cid)
            return {"status": "ok", "name": c["name"], "vault": c["vault_balance"],
                    "share": share, "members": len(ids)}


async def top_clans(pool):
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT c.name, c.level, c.vault_balance, "
            "(SELECT COUNT(*) FROM clan_members m WHERE m.clan_id = c.id) AS members "
            "FROM clans c ORDER BY c.vault_balance DESC, c.id LIMIT 10")

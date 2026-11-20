"""Step 2C idempotent migration: add bot_id to games, clans, and chat events tables.

Tables migrated:
- bluff_games
- mafia_stats
- clans
- clan_members
- blood_pacts
- bounties

Safe to run multiple times. Never drops, renames, retypes, or alters primary keys.
Never logs tokens or database URLs.
"""
import asyncio
import logging
import os

import asyncpg
from dotenv import load_dotenv

import bot_context

log = logging.getLogger(__name__)

BATCH_SIZE = 1000

STEP2C_TABLES = (
    "bluff_games",
    "mafia_stats",
    "clans",
    "clan_members",
    "blood_pacts",
    "bounties",
)

STEP2C_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_bluff_games_bot_chat ON bluff_games (bot_id, chat_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_mafia_stats_bot_wins ON mafia_stats (bot_id, wins DESC)",
    "CREATE INDEX IF NOT EXISTS idx_clans_bot_vault ON clans (bot_id, vault_balance DESC)",
    "CREATE INDEX IF NOT EXISTS idx_clan_members_bot_user ON clan_members (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_clan_members_bot_clan ON clan_members (bot_id, clan_id)",
    "CREATE INDEX IF NOT EXISTS idx_blood_pacts_bot_p1 ON blood_pacts (bot_id, player1_id)",
    "CREATE INDEX IF NOT EXISTS idx_blood_pacts_bot_p2 ON blood_pacts (bot_id, player2_id)",
    "CREATE INDEX IF NOT EXISTS idx_bounties_bot_target ON bounties (bot_id, target_id)",
)


def _rows_affected(status: str) -> int:
    parts = (status or "").split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return 0


async def _migrate_table(pool: asyncpg.Pool, table: str, main_bot_id: int) -> int:
    async with pool.acquire() as conn:
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS bot_id BIGINT NOT NULL DEFAULT 0"
        )

    total_updated = 0
    while True:
        async with pool.acquire() as conn:
            status = await conn.execute(
                f"UPDATE {table} SET bot_id = $1 "
                f"WHERE ctid IN (SELECT ctid FROM {table} WHERE bot_id = 0 LIMIT $2)",
                main_bot_id,
                BATCH_SIZE,
            )
        count = _rows_affected(status)
        total_updated += count
        if count < BATCH_SIZE:
            break
        await asyncio.sleep(0.02)
    return total_updated


async def run_step2c_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    """Run Step 2C migration safely. Never raises so main Rumi can still start."""
    if not main_bot_id or main_bot_id <= 0:
        log.error("Step 2C migration skipped: invalid main_bot_id")
        return False

    ok = True
    log.info("Step 2C migration starting for bot_id=%s", main_bot_id)

    for table in STEP2C_TABLES:
        try:
            updated = await _migrate_table(pool, table, main_bot_id)
            if updated:
                log.info("Step 2C migration: %s backfilled %d rows", table, updated)
        except Exception as exc:
            ok = False
            log.error("Step 2C migration failed on table %s: %s", table, type(exc).__name__)

    for idx_sql in STEP2C_INDEXES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(idx_sql)
        except Exception as exc:
            ok = False
            log.error("Step 2C migration index failed: %s", type(exc).__name__)

    if ok:
        log.info("Step 2C migration completed cleanly.")
    else:
        log.error("Step 2C migration finished with errors; check logs above.")
    return ok


async def _cli() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    import db

    token = os.environ.get("BOT_TOKEN", "")
    main_bot_id = bot_context.parse_bot_id_from_token(token)
    pool = await db.init_pool()
    try:
        await run_step2c_migration(pool, main_bot_id)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())

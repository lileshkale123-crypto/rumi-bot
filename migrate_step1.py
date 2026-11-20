"""Step 1 idempotent migration: add bot_id to core economy tables, backfill in batches, add indexes.

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

CORE_ECONOMY_TABLES = (
    "users",
    "wallets",
    "transactions",
    "streaks",
    "rob_cooldowns",
    "rob_xp_log",
    "rob_msg_log",
)

CORE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_users_bot_id ON users (bot_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_users_bot_username ON users (bot_id, lower(username))",
    "CREATE INDEX IF NOT EXISTS idx_wallets_bot_user ON wallets (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_wallets_bot_cash ON wallets (bot_id, cash DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wallets_bot_xp ON wallets (bot_id, xp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wallets_bot_gems ON wallets (bot_id, gems DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tx_bot_user ON transactions (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_tx_bot_created ON transactions (bot_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_streaks_bot_user ON streaks (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_rob_cd_bot_user ON rob_cooldowns (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_rob_xp_bot_user ON rob_xp_log (bot_id, user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rob_msg_bot_chat ON rob_msg_log (bot_id, chat_id, message_id)",
)


def _rows_affected(status: str) -> int:
    parts = (status or "").split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return 0


async def _migrate_table(pool: asyncpg.Pool, table: str, main_bot_id: int) -> int:
    """Add bot_id column if missing and backfill bot_id = 0 in small batches."""
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


async def run_step1_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    """Run Step 1 migration safely. Never raises so main Rumi can still start."""
    if not main_bot_id or main_bot_id <= 0:
        log.error("Step 1 migration skipped: invalid main_bot_id")
        return False

    ok = True
    log.info("Step 1 migration starting for bot_id=%s", main_bot_id)

    for table in CORE_ECONOMY_TABLES:
        try:
            updated = await _migrate_table(pool, table, main_bot_id)
            if updated:
                log.info("Step 1 migration: %s backfilled %d rows", table, updated)
        except Exception as exc:
            ok = False
            log.error("Step 1 migration failed on table %s: %s", table, type(exc).__name__)

    for idx_sql in CORE_INDEXES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(idx_sql)
        except Exception as exc:
            ok = False
            log.error("Step 1 migration index failed: %s", type(exc).__name__)

    if ok:
        log.info("Step 1 migration completed cleanly.")
    else:
        log.error("Step 1 migration finished with errors; check logs above.")
    return ok


async def _cli() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    import db

    token = os.environ.get("BOT_TOKEN", "")
    main_bot_id = bot_context.parse_bot_id_from_token(token)
    pool = await db.init_pool()
    try:
        await run_step1_migration(pool, main_bot_id)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())

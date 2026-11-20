"""Step 2D-1 idempotent migration: add bot_id to moderation, locks, blocklist, and raid tables.

Tables migrated:
- ax_warns
- ax_warn_settings
- ax_warn_log
- ax_temp_punish
- ax_approved
- ax_locks
- ax_lock_settings
- ax_allowlist
- ax_blocklist
- ax_blocklist_settings
- ax_raid_settings

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

STEP2D1_TABLES = (
    "ax_warns",
    "ax_warn_settings",
    "ax_warn_log",
    "ax_temp_punish",
    "ax_approved",
    "ax_locks",
    "ax_lock_settings",
    "ax_allowlist",
    "ax_blocklist",
    "ax_blocklist_settings",
    "ax_raid_settings",
)

STEP2D1_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ax_warns_bot_chat ON ax_warns (bot_id, chat_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_warn_settings_bot_chat ON ax_warn_settings (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_warn_log_bot_chat ON ax_warn_log (bot_id, chat_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_temp_punish_bot_exp ON ax_temp_punish (bot_id, expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_ax_approved_bot_chat ON ax_approved (bot_id, chat_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_locks_bot_chat ON ax_locks (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_lock_settings_bot_chat ON ax_lock_settings (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_allowlist_bot_chat ON ax_allowlist (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_blocklist_bot_chat ON ax_blocklist (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_blocklist_settings_bot_chat ON ax_blocklist_settings (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_raid_settings_bot_chat ON ax_raid_settings (bot_id, chat_id)",
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


async def run_step2d1_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    """Run Step 2D-1 migration safely. Never raises so main Rumi can still start."""
    if not main_bot_id or main_bot_id <= 0:
        log.error("Step 2D-1 migration skipped: invalid main_bot_id")
        return False

    ok = True
    log.info("Step 2D-1 migration starting for bot_id=%s", main_bot_id)

    for table in STEP2D1_TABLES:
        try:
            updated = await _migrate_table(pool, table, main_bot_id)
            if updated:
                log.info("Step 2D-1 migration: %s backfilled %d rows", table, updated)
        except Exception as exc:
            ok = False
            log.error("Step 2D-1 migration failed on table %s: %s", table, type(exc).__name__)

    for idx_sql in STEP2D1_INDEXES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(idx_sql)
        except Exception as exc:
            ok = False
            log.error("Step 2D-1 migration index failed: %s", type(exc).__name__)

    if ok:
        log.info("Step 2D-1 migration completed cleanly.")
    else:
        log.error("Step 2D-1 migration finished with errors; check logs above.")
    return ok


async def _cli() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    import db

    token = os.environ.get("BOT_TOKEN", "")
    main_bot_id = bot_context.parse_bot_id_from_token(token)
    pool = await db.init_pool()
    try:
        await run_step2d1_migration(pool, main_bot_id)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())

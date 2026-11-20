"""Step 2A idempotent migration: add bot_id to core infrastructure tables.

Tables migrated:
- ax_groups
- ax_chat_members
- ax_user_stats
- ax_bot_bans
- ax_user_lang
- ax_chat_lang

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

STEP2A_TABLES = (
    "ax_groups",
    "ax_chat_members",
    "ax_user_stats",
    "ax_bot_bans",
    "ax_user_lang",
    "ax_chat_lang",
)

STEP2A_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ax_groups_bot_chat ON ax_groups (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_chat_members_bot_chat ON ax_chat_members (bot_id, chat_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_user_stats_bot_user ON ax_user_stats (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_bot_bans_bot_user ON ax_bot_bans (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_user_lang_bot_user ON ax_user_lang (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_chat_lang_bot_chat ON ax_chat_lang (bot_id, chat_id)",
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


async def run_step2a_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    """Run Step 2A migration safely. Never raises so main Rumi can still start."""
    if not main_bot_id or main_bot_id <= 0:
        log.error("Step 2A migration skipped: invalid main_bot_id")
        return False

    ok = True
    log.info("Step 2A migration starting for bot_id=%s", main_bot_id)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS ax_chat_lang ("
                "chat_id BIGINT PRIMARY KEY, "
                "lang TEXT NOT NULL DEFAULT 'en')"
            )
    except Exception as exc:
        ok = False
        log.error("Step 2A failed ensuring ax_chat_lang: %s", type(exc).__name__)

    for table in STEP2A_TABLES:
        try:
            updated = await _migrate_table(pool, table, main_bot_id)
            if updated:
                log.info("Step 2A migration: %s backfilled %d rows", table, updated)
        except Exception as exc:
            ok = False
            log.error("Step 2A migration failed on table %s: %s", table, type(exc).__name__)

    for idx_sql in STEP2A_INDEXES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(idx_sql)
        except Exception as exc:
            ok = False
            log.error("Step 2A migration index failed: %s", type(exc).__name__)

    if ok:
        log.info("Step 2A migration completed cleanly.")
    else:
        log.error("Step 2A migration finished with errors; check logs above.")
    return ok


async def _cli() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    import db

    token = os.environ.get("BOT_TOKEN", "")
    main_bot_id = bot_context.parse_bot_id_from_token(token)
    pool = await db.init_pool()
    try:
        await run_step2a_migration(pool, main_bot_id)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())

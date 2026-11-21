"""Patch 1 idempotent migration: Season exchange/legends, recovery/merge tables, and indexes.

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

PATCH1_TABLES = (
    "ax_season_gem_conv",
    "ax_season_legends",
    "ax_recovery_auth",
    "ax_account_merges",
)

PATCH1_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ax_season_gem_conv_bot_user ON ax_season_gem_conv (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_season_legends_bot_cash ON ax_season_legends (bot_id, season_tag, cash_snapshot DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ax_recovery_auth_bot_user ON ax_recovery_auth (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_account_merges_bot_new ON ax_account_merges (bot_id, new_user_id)",
)


def _rows_affected(status: str) -> int:
    parts = (status or "").split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return 0


async def _backfill_bot_id(pool: asyncpg.Pool, table: str, main_bot_id: int) -> int:
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


async def run_patch1_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    """Create Patch 1 tables & indexes idempotently. Never raises so startup continues."""
    if not main_bot_id or main_bot_id <= 0:
        log.error("Patch 1 migration skipped: invalid main_bot_id")
        return False

    ok = True
    log.info("Patch 1 migration starting for bot_id=%s", main_bot_id)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS ax_season_gem_conv ("
                "bot_id BIGINT NOT NULL DEFAULT 0, "
                "user_id BIGINT NOT NULL, "
                "converted_gems INT NOT NULL DEFAULT 0, "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "PRIMARY KEY (bot_id, user_id))"
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS ax_season_legends ("
                "id BIGSERIAL PRIMARY KEY, "
                "bot_id BIGINT NOT NULL DEFAULT 0, "
                "season_tag TEXT NOT NULL, "
                "user_id BIGINT NOT NULL, "
                "display_name TEXT NOT NULL, "
                "cash_snapshot BIGINT NOT NULL DEFAULT 0, "
                "gems_snapshot BIGINT NOT NULL DEFAULT 0, "
                "xp_snapshot BIGINT NOT NULL DEFAULT 0, "
                "level_snapshot INT NOT NULL DEFAULT 1, "
                "recorded_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS ax_recovery_auth ("
                "bot_id BIGINT NOT NULL DEFAULT 0, "
                "user_id BIGINT NOT NULL, "
                "password_salt TEXT NOT NULL, "
                "password_hash TEXT NOT NULL, "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "PRIMARY KEY (bot_id, user_id))"
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS ax_account_merges ("
                "id BIGSERIAL PRIMARY KEY, "
                "bot_id BIGINT NOT NULL DEFAULT 0, "
                "old_user_id BIGINT NOT NULL, "
                "new_user_id BIGINT NOT NULL, "
                "merged_cash BIGINT NOT NULL DEFAULT 0, "
                "merged_gems BIGINT NOT NULL DEFAULT 0, "
                "merged_premium BOOLEAN NOT NULL DEFAULT FALSE, "
                "merged_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "UNIQUE (bot_id, old_user_id))"
            )
    except Exception as exc:
        ok = False
        log.error("Patch 1 table creation failed: %s", type(exc).__name__)

    for table in PATCH1_TABLES:
        try:
            updated = await _backfill_bot_id(pool, table, main_bot_id)
            if updated:
                log.info("Patch 1 migration: %s backfilled %d rows", table, updated)
        except Exception as exc:
            ok = False
            log.error("Patch 1 backfill failed on %s: %s", table, type(exc).__name__)

    for idx_sql in PATCH1_INDEXES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(idx_sql)
        except Exception as exc:
            ok = False
            log.error("Patch 1 index failed: %s", type(exc).__name__)

    if ok:
        log.info("Patch 1 migration completed cleanly.")
    else:
        log.error("Patch 1 migration finished with errors; check logs above.")
    return ok


async def _cli() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    import db

    token = os.environ.get("BOT_TOKEN", "")
    main_bot_id = bot_context.parse_bot_id_from_token(token)
    pool = await db.init_pool()
    try:
        await run_patch1_migration(pool, main_bot_id)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())

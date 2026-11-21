"""Patch 2 idempotent migration: Premium-only Powers, daily kill/rob/gem caps & indexes."""
import asyncio
import logging
import os
import asyncpg
from dotenv import load_dotenv
import bot_context

log = logging.getLogger(__name__)
BATCH_SIZE = 1000
PATCH2_TABLES = ("ax_user_powers", "ax_daily_limits")
PATCH2_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ax_user_powers_bot_user ON ax_user_powers (bot_id, user_id, expires_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ax_daily_limits_bot_day ON ax_daily_limits (bot_id, day_key, user_id)",
)

def _rows_affected(status: str) -> int:
    parts = (status or "").split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0

async def _backfill_bot_id(pool: asyncpg.Pool, table: str, main_bot_id: int) -> int:
    async with pool.acquire() as conn:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS bot_id BIGINT NOT NULL DEFAULT 0")
    total_updated = 0
    while True:
        async with pool.acquire() as conn:
            status = await conn.execute(
                f"UPDATE {table} SET bot_id = $1 WHERE ctid IN (SELECT ctid FROM {table} WHERE bot_id = 0 LIMIT $2)",
                main_bot_id, BATCH_SIZE,
            )
        count = _rows_affected(status)
        total_updated += count
        if count < BATCH_SIZE:
            break
        await asyncio.sleep(0.02)
    return total_updated

async def run_patch2_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    if not main_bot_id or main_bot_id <= 0:
        return False
    ok = True
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS ax_user_powers ("
                "bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, power_key TEXT NOT NULL, "
                "expires_at TIMESTAMPTZ NOT NULL, activated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "PRIMARY KEY (bot_id, user_id, power_key))"
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS ax_daily_limits ("
                "bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, day_key DATE NOT NULL, "
                "kills_used INT NOT NULL DEFAULT 0, robs_used INT NOT NULL DEFAULT 0, gems_used INT NOT NULL DEFAULT 0, "
                "PRIMARY KEY (bot_id, user_id, day_key))"
            )
    except Exception:
        ok = False
    for table in PATCH2_TABLES:
        try:
            await _backfill_bot_id(pool, table, main_bot_id)
        except Exception:
            ok = False
    for idx_sql in PATCH2_INDEXES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(idx_sql)
        except Exception:
            ok = False
    return ok

async def _cli() -> None:
    load_dotenv()
    import db
    main_bot_id = bot_context.parse_bot_id_from_token(os.environ.get("BOT_TOKEN", ""))
    pool = await db.init_pool()
    try:
        await run_patch2_migration(pool, main_bot_id)
    finally:
        await db.close_pool()

if __name__ == "__main__":
    asyncio.run(_cli())

"""Step 2B idempotent migration: add bot_id to economy add-on tables.

Tables migrated:
- ax_shop_owned
- ax_gift_inv
- ax_gift_recv
- ax_premium
- ax_donations
- ax_referrals
- ax_ref_bonus
- ax_group_rewards
- ax_kill_status
- ax_ship_cooldowns
- ax_couple_of_day

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

STEP2B_TABLES = (
    "ax_shop_owned",
    "ax_gift_inv",
    "ax_gift_recv",
    "ax_premium",
    "ax_donations",
    "ax_referrals",
    "ax_ref_bonus",
    "ax_group_rewards",
    "ax_kill_status",
    "ax_ship_cooldowns",
    "ax_couple_of_day",
)

STEP2B_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ax_shop_owned_bot_user ON ax_shop_owned (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_gift_inv_bot_user ON ax_gift_inv (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_gift_recv_bot_user ON ax_gift_recv (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_premium_bot_user ON ax_premium (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_donations_bot_id ON ax_donations (bot_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ax_referrals_bot_ref ON ax_referrals (bot_id, referrer_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_ref_bonus_bot_ref ON ax_ref_bonus (bot_id, referrer_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_group_rew_bot_chat ON ax_group_rewards (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_kill_bot_user ON ax_kill_status (bot_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_ship_cd_bot_chat ON ax_ship_cooldowns (bot_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_ax_couple_bot_chat ON ax_couple_of_day (bot_id, chat_id)",
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


async def run_step2b_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    """Run Step 2B migration safely. Never raises so main Rumi can still start."""
    if not main_bot_id or main_bot_id <= 0:
        log.error("Step 2B migration skipped: invalid main_bot_id")
        return False

    ok = True
    log.info("Step 2B migration starting for bot_id=%s", main_bot_id)

    for table in STEP2B_TABLES:
        try:
            updated = await _migrate_table(pool, table, main_bot_id)
            if updated:
                log.info("Step 2B migration: %s backfilled %d rows", table, updated)
        except Exception as exc:
            ok = False
            log.error("Step 2B migration failed on table %s: %s", table, type(exc).__name__)

    for idx_sql in STEP2B_INDEXES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(idx_sql)
        except Exception as exc:
            ok = False
            log.error("Step 2B migration index failed: %s", type(exc).__name__)

    if ok:
        log.info("Step 2B migration completed cleanly.")
    else:
        log.error("Step 2B migration finished with errors; check logs above.")
    return ok


async def _cli() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    import db

    token = os.environ.get("BOT_TOKEN", "")
    main_bot_id = bot_context.parse_bot_id_from_token(token)
    pool = await db.init_pool()
    try:
        await run_step2b_migration(pool, main_bot_id)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())

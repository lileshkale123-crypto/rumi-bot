"""Patch 5 idempotent migration: daily group events & number guess."""
import asyncio, os, asyncpg
from dotenv import load_dotenv
import bot_context

async def run_patch5_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    if not main_bot_id or main_bot_id <= 0:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS ax_daily_events (bot_id BIGINT NOT NULL DEFAULT 0, chat_id BIGINT NOT NULL, day_key DATE NOT NULL, event_kind TEXT NOT NULL, triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, chat_id, day_key))")
            await conn.execute("CREATE TABLE IF NOT EXISTS ax_guess_rounds (bot_id BIGINT NOT NULL DEFAULT 0, chat_id BIGINT NOT NULL, secret_num INT NOT NULL, low_bound INT NOT NULL DEFAULT 100, high_bound INT NOT NULL DEFAULT 500, tries_used INT NOT NULL DEFAULT 0, max_tries INT NOT NULL DEFAULT 12, reward_coins INT NOT NULL DEFAULT 750, active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, chat_id))")
        return True
    except Exception:
        return False

async def _cli():
    load_dotenv()
    import db
    pool = await db.init_pool()
    try:
        await run_patch5_migration(pool, bot_context.parse_bot_id_from_token(os.environ.get("BOT_TOKEN", "")))
    finally:
        await db.close_pool()

if __name__ == "__main__":
    asyncio.run(_cli())

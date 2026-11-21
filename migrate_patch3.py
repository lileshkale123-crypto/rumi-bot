"""Patch 3 idempotent migration: tasks, exchanges, friends, intros, name history."""
import asyncio, logging, os, asyncpg
from dotenv import load_dotenv
import bot_context

log = logging.getLogger(__name__)
async def run_patch3_migration(pool: asyncpg.Pool, main_bot_id: int) -> bool:
    if not main_bot_id or main_bot_id <= 0:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS ax_daily_tasks (bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, day_key DATE NOT NULL, target_word TEXT NOT NULL, letters_json TEXT NOT NULL, action_type TEXT NOT NULL, action_target INT NOT NULL, action_progress INT NOT NULL DEFAULT 0, task1_done BOOLEAN NOT NULL DEFAULT FALSE, task2_done BOOLEAN NOT NULL DEFAULT FALSE, reward_claimed BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id, day_key))")
            await conn.execute("CREATE TABLE IF NOT EXISTS ax_word_exchanges (id BIGSERIAL PRIMARY KEY, bot_id BIGINT NOT NULL DEFAULT 0, day_key DATE NOT NULL, requester_id BIGINT NOT NULL, requester_name TEXT NOT NULL, target_id BIGINT NOT NULL, target_name TEXT NOT NULL, want_letter TEXT NOT NULL, give_letter TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            await conn.execute("CREATE TABLE IF NOT EXISTS ax_user_friends (bot_id BIGINT NOT NULL DEFAULT 0, owner_id BIGINT NOT NULL, friend_id BIGINT NOT NULL, tag_name TEXT NOT NULL, added_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, owner_id, friend_id))")
            await conn.execute("CREATE TABLE IF NOT EXISTS ax_user_intros (bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, intro_text TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id))")
            await conn.execute("CREATE TABLE IF NOT EXISTS ax_name_history (bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, names_json TEXT NOT NULL DEFAULT '[]', usernames_json TEXT NOT NULL DEFAULT '[]', updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id))")
        return True
    except Exception:
        return False

async def _cli():
    load_dotenv()
    import db
    pool = await db.init_pool()
    try:
        await run_patch3_migration(pool, bot_context.parse_bot_id_from_token(os.environ.get("BOT_TOKEN", "")))
    finally:
        await db.close_pool()

if __name__ == "__main__":
    asyncio.run(_cli())

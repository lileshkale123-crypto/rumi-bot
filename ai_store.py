from datetime import datetime, timedelta, timezone

import config

IST = timezone(timedelta(hours=5, minutes=30))


async def use_quota(pool, user_id: int) -> bool:
    """Counts one message. Returns False if the user is over today's limit."""
    today = datetime.now(IST).date()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ai_usage (user_id, day, count) VALUES ($1, $2, 1)
            ON CONFLICT (user_id, day) DO UPDATE SET count = ai_usage.count + 1
            WHERE ai_usage.count < $3
            RETURNING count
            """,
            user_id, today, config.AI_DAILY_LIMIT,
        )
    return row is not None


async def get_history(pool, chat_id: int) -> list[tuple[str, str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, content FROM ai_memory WHERE chat_id = $1 ORDER BY id DESC LIMIT $2",
            chat_id, config.AI_HISTORY_MESSAGES,
        )
    rows = list(reversed(rows))
    while rows and rows[0]["role"] != "user":   # Gemini wants history to start with the user
        rows.pop(0)
    return [(r["role"], r["content"]) for r in rows]


async def save_turn(pool, chat_id: int, user_text: str, reply: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO ai_memory (chat_id, role, content) VALUES ($1, 'user', $2), ($1, 'model', $3)",
                chat_id, user_text, reply,
            )
            await conn.execute(
                "DELETE FROM ai_memory WHERE chat_id = $1 AND id NOT IN "
                "(SELECT id FROM ai_memory WHERE chat_id = $1 ORDER BY id DESC LIMIT 40)",
                chat_id,
            )

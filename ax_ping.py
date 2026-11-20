"""/ping latency check for Postgres and Telegram Bot API (aiogram 3.x)."""
import time

import asyncpg
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="ax_ping")


@router.message(Command("ping"))
async def ping_command(message: Message, pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        t0 = time.perf_counter()
        for _ in range(5):
            await c.fetchval("SELECT 1")
        db_ms = (time.perf_counter() - t0) * 1000 / 5

    t1 = time.perf_counter()
    sent = await message.reply("🏓 Pinging...")
    api_ms = (time.perf_counter() - t1) * 1000
    await sent.edit_text(
        f"🏓 <b>Pong</b>\n<blockquote>🗄 Database: <b>{db_ms:.0f} ms</b>\n"
        f"📡 Telegram: <b>{api_ms:.0f} ms</b></blockquote>",
        parse_mode=ParseMode.HTML,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

import asyncio
import importlib
import inspect
import logging
import os
from datetime import datetime, timedelta, timezone
from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.types import TelegramObject, Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
import bot_context, db, keepalive
import migrate_step1, migrate_step2a, migrate_step2b, migrate_step2c, migrate_step2d1

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
POLLING_UPDATES = ["message", "callback_query"]
MODULE_ORDER = ["ax_premium", "handlers"]

async def on_startup(bot: Bot, dispatcher: Dispatcher) -> None:
    pool = await db.init_pool()
    dispatcher["pool"] = pool
    me = await bot.me()
    dispatcher["bot_id"] = me.id
    await migrate_step2d1.run_step2d1_migration(pool, me.id)
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    if not scheduler.running:
        scheduler.start()

async def run_bot() -> None:
    token = os.environ["BOT_TOKEN"]
    bot = Bot(token=token)
    dp = Dispatcher()
    dp["scheduler"] = AsyncIOScheduler(timezone=timezone.utc)
    dp.startup.register(on_startup)
    await dp.start_polling(bot, allowed_updates=POLLING_UPDATES)

if __name__ == "__main__":
    asyncio.run(run_bot())

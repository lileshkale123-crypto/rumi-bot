import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        t0 = time.perf_counter()
        for _ in range(5):
            await c.fetchval("SELECT 1")
        db_ms = (time.perf_counter() - t0) * 1000 / 5
    t1 = time.perf_counter()
    sent = await msg.reply_text("🏓 Pinging...")
    api_ms = (time.perf_counter() - t1) * 1000
    await sent.edit_text(
        f"🏓 <b>Pong</b>\n<blockquote>🗄 Database: <b>{db_ms:.0f} ms</b>\n"
        f"📡 Telegram: <b>{api_ms:.0f} ms</b></blockquote>",
        parse_mode="HTML",
    )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("ping", ping_command))

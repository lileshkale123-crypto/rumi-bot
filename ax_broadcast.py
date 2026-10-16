"""Owner-only: broadcast a message to every group Rumi is in."""
import asyncio
import html

from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_adminpanel import OWNER_IDS

H = ParseMode.HTML


async def broadcast_cmd(update, context):
    user = update.effective_user
    if user is None or user.id not in OWNER_IDS:
        return
    text = update.effective_message.text or ""
    parts = text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.effective_message.reply_text("Usage: /broadcast <message>")
        return
    body = parts[1].strip()
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT chat_id, title FROM ax_groups")
    status = await update.effective_message.reply_text(
        f"\U0001f4e2 Sending to {len(rows)} groups...")
    sent, failed = 0, 0
    for r in rows:
        try:
            await context.bot.send_message(
                r["chat_id"],
                f"\U0001f4e2 <b>Announcement</b>\n\n{body}",
                parse_mode=H)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(
        f"\U0001f4e2 Broadcast done.\nSent: {sent}  Failed: {failed}  Total: {len(rows)}")


def register(app):
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

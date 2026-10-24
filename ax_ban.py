"""Owner-only: ban a user from all fun/economy commands bot-wide."""
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, CommandHandler, MessageHandler, filters

from ax_adminpanel import OWNER_IDS
from ax_funmode import FUN_CMDS

H = ParseMode.HTML
_banned = set()


async def _load(pool):
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT user_id FROM ax_bot_bans")
    _banned.clear()
    _banned.update(r["user_id"] for r in rows)


async def banuser_cmd(update, context):
    user = update.effective_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Usage: /banuser user_id")
        return
    target_id = int(args[0])
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_bot_bans (user_id) VALUES ($1) ON CONFLICT DO NOTHING", target_id)
    _banned.add(target_id)
    await update.effective_message.reply_text(
        f"\U0001f6ab Banned <code>{target_id}</code> from economy/games bot-wide.", parse_mode=H)


async def unbanuser_cmd(update, context):
    user = update.effective_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Usage: /unbanuser user_id")
        return
    target_id = int(args[0])
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_bot_bans WHERE user_id=$1", target_id)
    _banned.discard(target_id)
    await update.effective_message.reply_text(
        f"\u2705 Unbanned <code>{target_id}</code>.", parse_mode=H)


async def _gate(update, context):
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or not msg.text:
        return
    if user.id not in _banned:
        return
    cmd = msg.text[1:].split()[0].split("@")[0].lower() if msg.text.startswith("/") else ""
    if cmd in FUN_CMDS:
        raise ApplicationHandlerStop


def register(app):
    app.add_handler(CommandHandler("banuser", banuser_cmd))
    app.add_handler(CommandHandler("unbanuser", unbanuser_cmd))
    app.add_handler(MessageHandler(filters.COMMAND, _gate), group=-6)


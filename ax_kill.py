"""Fun command: /kill as a reply gives a random cash reward (160-270)."""
import random

from telegram.constants import ParseMode
from telegram.ext import CommandHandler

import services

H = ParseMode.HTML
MIN_KILL = 161
MAX_KILL = 270


async def kill_cmd(update, context):
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot:
        await msg.reply_text("Reply to someone's message with /kill 🔪")
        return
    pool = context.bot_data["pool"]
    amount = random.randint(MIN_KILL, MAX_KILL)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)
    async with pool.acquire() as c:
        w = await c.fetchrow("SELECT cash FROM wallets WHERE user_id=$1", user.id)
        new_cash = w["cash"] + amount
        await c.execute("UPDATE wallets SET cash=$1 WHERE user_id=$2", new_cash, user.id)
        await c.execute(
            "INSERT INTO transactions (user_id, type, currency, amount, balance_before, "
            "balance_after, related_user_id, source) VALUES ($1,'credit','cash',$2,$3,$4,$5,$6)",
            user.id, amount, w["cash"], new_cash, target.id, "kill_command")
    name = user.first_name or "Someone"
    victim = target.first_name or "them"
    await msg.reply_text(
        f"🔪 <b>{name}</b> took out <b>{victim}</b> and found <code>{amount}</code> cash on the body.",
        parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("kill", kill_cmd))

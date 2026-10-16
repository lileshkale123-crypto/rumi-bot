"""Hidden owner-only /givecoins. Silent for everyone else."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import esc
from services import ensure_user_and_wallet

OWNERS = {8151412244, 6584534013}
MAX_AMT = 1_000_000_000


async def givecoins_cmd(update, context):
    user = update.effective_user
    msg = update.effective_message
    if user is None or msg is None or user.id not in OWNERS:
        return
    try:
        amt = int(context.args[0])
    except Exception:
        await msg.reply_text("Usage: /givecoins amount (reply to someone, or alone for yourself)")
        return
    if amt < 1 or amt > MAX_AMT:
        await msg.reply_text("Amount must be between 1 and %d" % MAX_AMT)
        return
    target = user
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target = msg.reply_to_message.from_user
    if target.is_bot:
        await msg.reply_text("Bots have no wallet.")
        return
    pool = context.bot_data["pool"]
    await ensure_user_and_wallet(pool, target.id, target.username, target.first_name)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id = $2",
            amt, target.id)
    name = esc(target.first_name or "Friend")
    await msg.reply_text(
        "\U0001F4B0 <b>Done</b>\n<blockquote>%s received <b>%d</b> coins \u2728</blockquote>"
        % (name, amt), parse_mode=ParseMode.HTML)


def register(app):
    app.add_handler(CommandHandler("givecoins", givecoins_cmd))

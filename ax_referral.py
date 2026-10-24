"""Referral system: /start ref_<id> tracks it, reward paid on new user's first /claim."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

H = ParseMode.HTML
REWARD = 500


async def _capture_start(update, context):
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or not msg.text:
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].startswith("ref_"):
        return
    ref_part = parts[1][4:]
    if not ref_part.isdigit():
        return
    referrer_id = int(ref_part)
    if referrer_id == user.id:
        return
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        existing = await c.fetchval(
            "SELECT 1 FROM users WHERE id=$1", user.id)
        if existing:
            return
        await c.execute(
            "INSERT INTO ax_referrals (new_user_id, referrer_id) VALUES ($1,$2) "
            "ON CONFLICT DO NOTHING", user.id, referrer_id)


async def _pay_on_first_claim(pool, bot, user_id):
    referrer_id = None
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "SELECT referrer_id FROM ax_referrals WHERE new_user_id=$1 AND rewarded=FALSE FOR UPDATE",
                user_id)
            if row is None:
                return
            referrer_id = row["referrer_id"]
            for uid in sorted({user_id, referrer_id}):
                w = await c.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
                if w is None:
                    continue
                new_cash = w["cash"] + REWARD
                await c.execute("UPDATE wallets SET cash=$1, updated_at=now() WHERE user_id=$2", new_cash, uid)
                await c.execute(
                    "INSERT INTO transactions (user_id, type, currency, amount, balance_before, "
                    "balance_after, source) VALUES ($1,'credit','cash',$2,$3,$4,$5)",
                    uid, REWARD, w["cash"], new_cash, "referral_bonus")
            await c.execute(
                "UPDATE ax_referrals SET rewarded=TRUE WHERE new_user_id=$1", user_id)
    if referrer_id:
        try:
            await bot.send_message(
                referrer_id,
                f"\U0001f389 Your referral played Rumi! You both earned <code>{REWARD}</code> cash.",
                parse_mode=H)
        except Exception:
            pass


async def referral_cmd(update, context):
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        count = await c.fetchval(
            "SELECT COUNT(*) FROM ax_referrals WHERE referrer_id=$1 AND rewarded=TRUE", user.id)
    link = f"https://t.me/{context.bot.username}?start=ref_{user.id}"
    await msg.reply_text(
        f"\U0001f91d <b>Invite friends, earn cash</b>\n\n"
        f"Share your link \u2014 when a friend joins and plays, you both get "
        f"<code>{REWARD}</code> cash.\n\n"
        f"<code>{link}</code>\n\n"
        f"Successful invites: <b>{count}</b>",
        parse_mode=H)


def register(app):
    app.add_handler(MessageHandler(filters.Regex(r"^/start ref_") & filters.ChatType.PRIVATE,
                                    _capture_start), group=-9)
    app.add_handler(CommandHandler("referral", referral_cmd))

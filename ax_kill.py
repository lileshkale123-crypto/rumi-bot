"""Fun command: /kill (reply) sends target dead for ~8h. /revive pays to come back early."""
import random
from datetime import datetime, timedelta, timezone

from telegram.constants import ParseMode
from telegram.ext import CommandHandler

import services
from ax_common import esc

H = ParseMode.HTML
MIN_KILL = 161
MAX_KILL = 270
DEAD_HOURS_MIN = 7
DEAD_HOURS_MAX = 8
REVIVE_MIN = 300
REVIVE_MAX = 400


async def _dead_until(pool, user_id):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT dead_until FROM ax_kill_status WHERE user_id=$1", user_id)
    if row is None:
        return None
    if row["dead_until"] <= datetime.now(timezone.utc):
        return None
    return row["dead_until"]


async def kill_cmd(update, context):
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return
    pool = context.bot_data["pool"]

    killer_dead = await _dead_until(pool, user.id)
    if killer_dead:
        await msg.reply_text(
            "\U0001f480 You're dead right now. Use /revive to come back, or wait it out.")
        return

    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot:
        await msg.reply_text("Reply to someone's message with /kill \U0001f52a")
        return
    if target.id == user.id:
        await msg.reply_text("You can't kill yourself.")
        return

    target_dead = await _dead_until(pool, target.id)
    if target_dead:
        name = target.first_name or "They"
        await msg.reply_text(f"\U0001f480 {name} is already dead.")
        return

    _tw = await services.get_wallet(pool, target.id)
    _sh = _tw["shield_until"] if _tw else None
    if _sh and _sh > datetime.now(timezone.utc):
        import ui
        await msg.reply_text(
            "\U0001f6e1 <b>%s is protected</b>\n<blockquote>Their Guard is active, "
            "so nobody can kill them. Time left: <code>%s</code></blockquote>"
            % (esc(target.first_name or "They"), ui._time_remaining(_sh)),
            parse_mode=H)
        return

    hours = random.uniform(DEAD_HOURS_MIN, DEAD_HOURS_MAX)
    dead_until = datetime.now(timezone.utc) + timedelta(hours=hours)
    amount = random.randint(MIN_KILL, MAX_KILL)

    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)
    async with pool.acquire() as c:
        async with c.transaction():
            w = await c.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", user.id)
            new_cash = w["cash"] + amount
            await c.execute("UPDATE wallets SET cash=$1, updated_at=now() WHERE user_id=$2", new_cash, user.id)
            await c.execute(
                "INSERT INTO transactions (user_id, type, currency, amount, balance_before, "
                "balance_after, related_user_id, source) VALUES ($1,'credit','cash',$2,$3,$4,$5,$6)",
                user.id, amount, w["cash"], new_cash, target.id, "kill_command")
            await c.execute(
                "INSERT INTO ax_kill_status (user_id, dead_until) VALUES ($1,$2) "
                "ON CONFLICT (user_id) DO UPDATE SET dead_until=$2", target.id, dead_until)

    name = esc(user.first_name or "Someone")
    victim = esc(target.first_name or "them")
    line1 = f"👤 <b>{name}</b> killed <b>{victim}</b>!"
    line2 = f"💰 Earned: <code>{amount}</code> cash"
    await msg.reply_text(line1 + "\n" + line2, parse_mode=H)


async def revive_cmd(update, context):
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return
    pool = context.bot_data["pool"]
    dead_until = await _dead_until(pool, user.id)
    if not dead_until:
        await msg.reply_text("You're not dead.")
        return
    cost = random.randint(REVIVE_MIN, REVIVE_MAX)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)
    async with pool.acquire() as c:
        async with c.transaction():
            w = await c.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", user.id)
            if w["cash"] < cost:
                await msg.reply_text(
                    f"\U0001f480 Reviving costs <code>{cost}</code> cash. "
                    f"You have <code>{w['cash']}</code>.", parse_mode=H)
                return
            new_cash = w["cash"] - cost
            await c.execute("UPDATE wallets SET cash=$1, updated_at=now() WHERE user_id=$2", new_cash, user.id)
            await c.execute(
                "INSERT INTO transactions (user_id, type, currency, amount, balance_before, "
                "balance_after, source) VALUES ($1,'debit','cash',$2,$3,$4,$5)",
                user.id, cost, w["cash"], new_cash, "revive_command")
            await c.execute("DELETE FROM ax_kill_status WHERE user_id=$1", user.id)
    await msg.reply_text(
        f"\u2728 You're back! Paid <code>{cost}</code> cash to revive.", parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("kill", kill_cmd))
    app.add_handler(CommandHandler("revive", revive_cmd))

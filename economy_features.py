"""Vault, /work, /spin and /mafiatop for Rumi."""

import random
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
import services
import ui

CUR = config.CURRENCY_SYMBOL


def _n(v) -> str:
    return f"{int(v):,}"


def _left(target: datetime) -> str:
    secs = max(0, int((target - datetime.now(timezone.utc)).total_seconds()))
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def vault_cap(level: int) -> int:
    return config.VAULT_BASE_CAPACITY + level * config.VAULT_CAPACITY_PER_LEVEL


async def _prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    pool = context.bot_data["pool"]
    await services.ensure_user_and_wallet(pool, u.id, u.username, u.first_name)
    return u, pool


async def vault_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u, pool = await _prep(update, context)
    w = await services.get_wallet(pool, u.id)
    cap = vault_cap(w["level"])
    text = (
        "🏦 <b>THE UNDERWORLD VAULT</b>\n"
        "<blockquote>"
        f"👤 <b>Client:</b> {ui.escape_html(u.first_name or 'Player')}\n"
        f"📈 <b>Vault Level:</b> {w['level']}\n"
        f"💰 <b>Liquid Cash:</b> <code>{CUR}{_n(w['cash'])}</code>\n"
        f"🔐 <b>Secured Assets:</b> <code>{CUR}{_n(w['vault'])} / {CUR}{_n(cap)}</code>"
        "</blockquote>\n"
        "<i>Use /deposit [amount] or /withdraw [amount].</i>"
    )
    await update.message.reply_html(text)


def _parse_amount(args):
    """Returns (ok, amount). amount None means 'all'."""
    if not args:
        return False, 0
    a = args[0].lower().replace(",", "")
    if a == "all":
        return True, None
    if a.isdigit() and int(a) > 0:
        return True, int(a)
    return False, 0


async def _move(pool, uid: int, amount, deposit: bool) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            w = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", uid)
            if w is None:
                return {"status": "error"}
            cap = vault_cap(w["level"])
            cash, vault = w["cash"], w["vault"]
            if deposit:
                room = cap - vault
                if room <= 0:
                    return {"status": "full", "cap": cap}
                if amount is None:
                    amount = min(cash, room)
                if amount <= 0 or amount > cash:
                    return {"status": "no_cash"}
                if amount > room:
                    return {"status": "over_cap", "room": room}
                new_cash, new_vault = cash - amount, vault + amount
                t, src, delta = "VAULT_DEPOSIT", "deposit_command", -amount
            else:
                if amount is None:
                    amount = vault
                if amount <= 0 or amount > vault:
                    return {"status": "no_vault"}
                new_cash, new_vault = cash + amount, vault - amount
                t, src, delta = "VAULT_WITHDRAW", "withdraw_command", amount
            await conn.execute(
                "UPDATE wallets SET cash = $1, vault = $2, updated_at = now() WHERE user_id = $3",
                new_cash, new_vault, uid,
            )
            await services.insert_transaction(
                conn, user_id=uid, type_=t, currency="cash", amount=delta,
                balance_before=cash, balance_after=new_cash, source=src,
                metadata={"vault_after": new_vault},
            )
            return {"status": "ok", "amount": amount, "cash": new_cash, "vault": new_vault, "cap": cap}


async def _vault_move(update: Update, context: ContextTypes.DEFAULT_TYPE, deposit: bool) -> None:
    u, pool = await _prep(update, context)
    cmd = "deposit" if deposit else "withdraw"
    ok, amount = _parse_amount(context.args)
    if not ok:
        await update.message.reply_html(ui.error_card(f"Use /{cmd} [amount] or /{cmd} all"))
        return
    r = await _move(pool, u.id, amount, deposit)
    s = r["status"]
    if s == "ok":
        title = "DEPOSIT SECURED" if deposit else "WITHDRAWAL COMPLETE"
        icon = "🔐" if deposit else "💸"
        await update.message.reply_html(
            f"🏦 <b>{title}</b>\n<blockquote>"
            f"{icon} <b>Amount:</b> <code>{CUR}{_n(r['amount'])}</code>\n"
            f"💰 <b>Liquid Cash:</b> <code>{CUR}{_n(r['cash'])}</code>\n"
            f"🔐 <b>Secured:</b> <code>{CUR}{_n(r['vault'])} / {CUR}{_n(r['cap'])}</code>"
            "</blockquote>"
        )
    elif s == "full":
        await update.message.reply_html(ui.error_card(f"Your vault is full ({CUR}{_n(r['cap'])}). Level up to store more."))
    elif s == "over_cap":
        await update.message.reply_html(ui.error_card(f"Your vault only has room for {CUR}{_n(r['room'])} more."))
    elif s == "no_cash":
        await update.message.reply_html(ui.error_card("You don't have that much cash to deposit."))
    elif s == "no_vault":
        await update.message.reply_html(ui.error_card("You don't have that much in your vault."))
    else:
        await update.message.reply_html(ui.error_card("Something went wrong. Please try again."))


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _vault_move(update, context, True)


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _vault_move(update, context, False)


import asyncio
from datetime import timedelta

JOBS = [
    "Smuggled contraband across the southern border.",
    "Collected debts for the Don, no questions asked.",
    "Guarded a midnight shipment at the docks.",
    "Forged papers for a runaway lord.",
    "Delivered a sealed envelope to the shadow court.",
    "Cracked a merchant's vault while the city slept.",
    "Sold rumors to the highest bidder.",
    "Drove the getaway carriage through the fog.",
]


async def _do_work(pool, uid: int) -> dict:
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            w = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", uid)
            if w is None:
                return {"status": "error"}
            last = w["last_work_at"]
            if last and (now - last).total_seconds() < config.WORK_COOLDOWN:
                return {"status": "cooldown", "retry_at": last + timedelta(seconds=config.WORK_COOLDOWN)}
            pay = random.randint(config.WORK_MIN, config.WORK_MAX)
            new_cash = w["cash"] + pay
            await conn.execute(
                "UPDATE wallets SET cash = $1, last_work_at = $2, updated_at = now() WHERE user_id = $3",
                new_cash, now, uid,
            )
            await services.insert_transaction(
                conn, user_id=uid, type_="WORK", currency="cash", amount=pay,
                balance_before=w["cash"], balance_after=new_cash, source="work_command",
            )
            return {"status": "ok", "pay": pay, "cash": new_cash,
                    "next": now + timedelta(seconds=config.WORK_COOLDOWN)}


async def work_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u, pool = await _prep(update, context)
    r = await _do_work(pool, u.id)
    if r["status"] == "cooldown":
        await update.message.reply_html(f"⏳ <b>No contracts yet</b>\nNext one in {_left(r['retry_at'])}.")
    elif r["status"] == "ok":
        await update.message.reply_html(
            "💼 <b>SYNDICATE CONTRACT COMPLETED</b>\n<blockquote>"
            f"📝 <b>Task:</b> <i>{random.choice(JOBS)}</i>\n"
            f"💵 <b>Payout:</b> <code>+{CUR}{_n(r['pay'])} Coins</code>\n"
            f"💼 <b>New Wallet Balance:</b> <code>{CUR}{_n(r['cash'])}</code></blockquote>\n"
            f"⏳ <i>Next contract available in {_left(r['next'])}.</i>"
        )
    else:
        await update.message.reply_html(ui.error_card("Something went wrong. Please try again."))


def _roll():
    x = random.random()
    if x < 0.70:
        return "Shadow Purse", random.randint(100, 500), 0
    if x < 0.90:
        return "Gilded Chest", 1000, 0
    if x < 0.99:
        return "Gem Cache", 0, random.randint(1, 5)
    return "JACKPOT: Crown Vault", 5000, 10


async def _do_spin(pool, uid: int) -> dict:
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            w = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", uid)
            if w is None:
                return {"status": "error"}
            last = w["last_spin_at"]
            if last and (now - last).total_seconds() < config.SPIN_COOLDOWN:
                return {"status": "cooldown", "retry_at": last + timedelta(seconds=config.SPIN_COOLDOWN)}
            name, cash_p, gem_p = _roll()
            nc, ng = w["cash"] + cash_p, w["gems"] + gem_p
            await conn.execute(
                "UPDATE wallets SET cash = $1, gems = $2, last_spin_at = $3, updated_at = now() WHERE user_id = $4",
                nc, ng, now, uid,
            )
            if cash_p:
                await services.insert_transaction(
                    conn, user_id=uid, type_="SPIN", currency="cash", amount=cash_p,
                    balance_before=w["cash"], balance_after=nc, source="spin_command", metadata={"prize": name},
                )
            if gem_p:
                await services.insert_transaction(
                    conn, user_id=uid, type_="SPIN", currency="gems", amount=gem_p,
                    balance_before=w["gems"], balance_after=ng, source="spin_command", metadata={"prize": name},
                )
            return {"status": "ok", "name": name, "cash_p": cash_p, "gem_p": gem_p, "cash": nc, "gems": ng}


async def spin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u, pool = await _prep(update, context)
    r = await _do_spin(pool, u.id)
    if r["status"] == "cooldown":
        await update.message.reply_html(f"🎰 <b>The wheel is resting</b>\nSpin again in {_left(r['retry_at'])}.")
        return
    if r["status"] != "ok":
        await update.message.reply_html(ui.error_card("Something went wrong. Please try again."))
        return
    msg = await update.message.reply_html(
        "🎰 <b>WHEEL OF FATE</b>\n<blockquote><i>The wheel is spinning...\n"
        "Let the shadows decide your fortune.</i>\n🎲 <code>[ 🔄 | 🔄 | 🔄 ]</code></blockquote>"
    )
    await asyncio.sleep(2)
    parts = []
    if r["cash_p"]:
        parts.append(f"+{CUR}{_n(r['cash_p'])}")
    if r["gem_p"]:
        parts.append(f"+{r['gem_p']} 💎")
    try:
        await msg.edit_text(
            "🎰 <b>WHEEL OF FATE: RESULT</b>\n<blockquote>"
            f"🎯 <b>Fortune:</b> {r['name']}\n"
            f"💎 <b>Yield:</b> <code>{' and '.join(parts)}</code>\n"
            f"💼 <b>Updated Balance:</b> <code>{CUR}{_n(r['cash'])} | 💎 {r['gems']}</code></blockquote>\n"
            "⏳ <i>The wheel will turn again tomorrow.</i>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def record_mafia_result(pool, players, winners) -> None:
    """players/winners: lists of objects with .user_id and .name"""
    win_ids = {p.user_id for p in winners}
    async with pool.acquire() as conn:
        async with conn.transaction():
            for p in sorted(players, key=lambda x: x.user_id):
                await conn.execute(
                    "INSERT INTO users (id, first_name, created_at, updated_at) "
                    "VALUES ($1, $2, now(), now()) ON CONFLICT (id) DO NOTHING",
                    p.user_id, p.name,
                )
                won = 1 if p.user_id in win_ids else 0
                await conn.execute(
                    "INSERT INTO mafia_stats (user_id, wins, games, updated_at) "
                    "VALUES ($1, $2, 1, now()) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "wins = mafia_stats.wins + EXCLUDED.wins, "
                    "games = mafia_stats.games + 1, updated_at = now()",
                    p.user_id, won,
                )


async def mafiatop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT COALESCE(u.first_name, u.username, 'Player') AS name, s.wins "
            "FROM mafia_stats s JOIN users u ON u.id = s.user_id "
            "WHERE s.wins > 0 ORDER BY s.wins DESC, s.games ASC LIMIT 10"
        )
    if not rows:
        await update.message.reply_html(
            "🏆 <b>MAFIA: HALL OF FAME</b>\n<blockquote><i>No legends yet. Win a game to be the first!</i></blockquote>"
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"<code>#{i + 1:02d}</code>"
        lines.append(f"{mark} <b>{ui.escape_html(r['name'])}</b> — <code>{r['wins']} Wins</code>")
    await update.message.reply_html(
        "🏆 <b>MAFIA: HALL OF FAME</b>\n<blockquote>"
        "<i>The most ruthless syndicates in the city.</i>\n" + "\n".join(lines) + "</blockquote>"
    )

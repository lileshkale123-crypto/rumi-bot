"""Title shop: buy titles with cash or gems, equip one, shown on profile and rankings."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

import config
import ui

H = ParseMode.HTML
ITEMS = {
    "fresh": ("🌱", "Fresh Face", 20000, "cash"),
    "sweet": ("🎀", "Sweetheart", 50000, "cash"),
    "lucky": ("🎲", "Lucky Charm", 125000, "cash"),
    "shadow": ("🥷", "Shadow", 250000, "cash"),
    "maker": ("💸", "Money Maker", 500000, "cash"),
    "menace": ("🔪", "Menace", 750000, "cash"),
    "royal": ("👑", "Royal", 1000000, "cash"),
    "gemlord": ("💎", "Gem Lord", 150, "gems"),
    "moonlit": ("🌙", "Moonlit Legend", 400, "gems"),
    "chosen": ("🦋", "Rumi's Chosen", 1000, "gems"),
}


def price_txt(p, cur):
    return f"{config.CURRENCY_SYMBOL}{p:,}" if cur == "cash" else f"{p:,} gems"


async def get_title(pool, user_id):
    row = await pool.fetchrow(
        "SELECT item_id FROM ax_shop_owned WHERE user_id=$1 AND equipped", user_id)
    if row and row["item_id"] in ITEMS:
        e, n, _, _ = ITEMS[row["item_id"]]
        return f"{e} {n}"
    return None


async def _owned(pool, uid):
    rows = await pool.fetch(
        "SELECT item_id, equipped FROM ax_shop_owned WHERE user_id=$1", uid)
    return {r["item_id"]: r["equipped"] for r in rows}


async def _shop_view(pool, uid, name):
    owned = await _owned(pool, uid)
    w = await pool.fetchrow("SELECT cash, gems FROM wallets WHERE user_id=$1", uid)
    body, btns = [], []
    for iid, (e, n, p, c) in ITEMS.items():
        body.append(f"{e} <b>{n}</b> · " + ("✅ owned" if iid in owned else price_txt(p, c)))
        if iid not in owned:
            btns.append(InlineKeyboardButton(f"{e} {n}", callback_data=f"axshop:buy:{iid}:{uid}"))
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    text = (f"🛍 <b>Rumi Title Shop</b>\n\n<b>{ui.escape_html(name)}</b>, pick a title to show off 💜\n\n"
            f"<blockquote>{chr(10).join(body)}</blockquote>\n"
            f"You have <code>{config.CURRENCY_SYMBOL}{w['cash']:,}</code> and <code>{w['gems']:,}</code> gems\n"
            f"Use /titles to equip what you own.")
    return text, InlineKeyboardMarkup(rows) if rows else None


async def _titles_view(pool, uid, name):
    owned = await _owned(pool, uid)
    if not owned:
        return (f"🎀 <b>{ui.escape_html(name)}</b>, you own no titles yet 💜\n"
                f"Visit /shop to get your first one."), None
    rows = []
    for iid, eq in owned.items():
        if iid in ITEMS:
            e, n, _, _ = ITEMS[iid]
            label = f"✔ {e} {n}" if eq else f"{e} {n}"
            rows.append([InlineKeyboardButton(label, callback_data=f"axshop:eq:{iid}:{uid}")])
    rows.append([InlineKeyboardButton("🚫 Remove title", callback_data=f"axshop:un:none:{uid}")])
    text = (f"🎀 <b>Your Titles</b>\n\n<blockquote>Tap a title to wear it. "
            f"It shows on your /profile and in /rankings.</blockquote>")
    return text, InlineKeyboardMarkup(rows)


async def _buy(pool, uid, iid):
    if iid not in ITEMS:
        return "Unknown item."
    e, n, price, cur = ITEMS[iid]
    col = "cash" if cur == "cash" else "gems"
    async with pool.acquire() as c:
        async with c.transaction():
            w = await c.fetchrow("SELECT cash, gems FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
            if w is None:
                return "Send /start to Rumi first 💜"
            if await c.fetchval("SELECT 1 FROM ax_shop_owned WHERE user_id=$1 AND item_id=$2", uid, iid):
                return "You already own this ✅"
            before = w[col]
            if before < price:
                return "Not enough yet 💔 keep earning."
            await c.execute(f"UPDATE wallets SET {col}={col}-$1, updated_at=now() WHERE user_id=$2", price, uid)
            await c.execute(
                "INSERT INTO transactions (user_id,type,currency,amount,balance_before,balance_after,source) "
                "VALUES ($1,'debit',$2,$3,$4,$5,'shop_title')", uid, col, price, before, before - price)
            await c.execute("INSERT INTO ax_shop_owned (user_id,item_id) VALUES ($1,$2)", uid, iid)
    return f"Bought {e} {n} 💜 Use /titles to wear it."


async def _equip(pool, uid, iid):
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("UPDATE ax_shop_owned SET equipped=FALSE WHERE user_id=$1", uid)
            if iid != "none":
                await c.execute("UPDATE ax_shop_owned SET equipped=TRUE WHERE user_id=$1 AND item_id=$2", uid, iid)


async def shop_cmd(update, context):
    u = update.effective_user
    pool = context.bot_data["pool"]
    if await pool.fetchrow("SELECT 1 FROM wallets WHERE user_id=$1", u.id) is None:
        await update.effective_message.reply_text("Send /start to Rumi first 💜")
        return
    text, kb = await _shop_view(pool, u.id, u.first_name or "Player")
    await update.effective_message.reply_text(text, parse_mode=H, reply_markup=kb)


async def titles_cmd(update, context):
    u = update.effective_user
    text, kb = await _titles_view(context.bot_data["pool"], u.id, u.first_name or "Player")
    await update.effective_message.reply_text(text, parse_mode=H, reply_markup=kb)


async def shop_cb(update, context):
    q = update.callback_query
    parts = q.data.split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await q.answer()
        return
    _, act, iid, uid = parts
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("This menu belongs to someone else 💜", show_alert=True)
        return
    pool = context.bot_data["pool"]
    name = q.from_user.first_name or "Player"
    if act == "buy":
        await q.answer(await _buy(pool, uid, iid), show_alert=True)
        text, kb = await _shop_view(pool, uid, name)
    elif act in ("eq", "un"):
        await _equip(pool, uid, iid)
        await q.answer("Title updated 💜")
        text, kb = await _titles_view(pool, uid, name)
    else:
        await q.answer()
        return
    try:
        await q.edit_message_text(text, parse_mode=H, reply_markup=kb)
    except Exception:
        pass


def register(app):
    app.add_handler(CommandHandler("shop", shop_cmd))
    app.add_handler(CommandHandler("titles", titles_cmd))
    app.add_handler(CallbackQueryHandler(shop_cb, pattern=r"^axshop:"))


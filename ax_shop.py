"""Title shop: buy titles with cash or gems, equip one, shown on profile and rankings (aiogram 3.x)."""
import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import ui

router = Router(name="ax_shop")
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


def price_txt(p: int, cur: str) -> str:
    return f"{config.CURRENCY_SYMBOL}{p:,}" if cur == "cash" else f"{p:,} gems"


async def get_title(pool: asyncpg.Pool, user_id: int) -> str | None:
    row = await pool.fetchrow(
        "SELECT item_id FROM ax_shop_owned WHERE user_id=$1 AND equipped",
        user_id,
    )
    if row and row["item_id"] in ITEMS:
        e, n, _, _ = ITEMS[row["item_id"]]
        return f"{e} {n}"
    return None


async def _owned(pool: asyncpg.Pool, uid: int) -> dict[str, bool]:
    rows = await pool.fetch(
        "SELECT item_id, equipped FROM ax_shop_owned WHERE user_id=$1",
        uid,
    )
    return {r["item_id"]: r["equipped"] for r in rows}


async def _shop_view(
    pool: asyncpg.Pool,
    uid: int,
    name: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    owned = await _owned(pool, uid)
    w = await pool.fetchrow("SELECT cash, gems FROM wallets WHERE user_id=$1", uid)
    cash = w["cash"] if w else 0
    gems = w["gems"] if w else 0
    body, btns = [], []
    for iid, (e, n, p, c) in ITEMS.items():
        body.append(f"{e} <b>{n}</b> · " + ("✅ owned" if iid in owned else price_txt(p, c)))
        if iid not in owned:
            btns.append(InlineKeyboardButton(text=f"{e} {n}", callback_data=f"axshop:buy:{iid}:{uid}"))
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    text = (
        f"🛍 <b>Rumi Title Shop</b>\n\n<b>{ui.escape_html(name)}</b>, pick a title to show off 💜\n\n"
        f"<blockquote>{chr(10).join(body)}</blockquote>\n"
        f"You have <code>{config.CURRENCY_SYMBOL}{cash:,}</code> and <code>{gems:,}</code> gems\n"
        f"Use /titles to equip what you own."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _titles_view(
    pool: asyncpg.Pool,
    uid: int,
    name: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    owned = await _owned(pool, uid)
    if not owned:
        return (
            f"🎀 <b>{ui.escape_html(name)}</b>, you own no titles yet 💜\n"
            f"Visit /shop to get your first one."
        ), None
    rows = []
    for iid, eq in owned.items():
        if iid in ITEMS:
            e, n, _, _ = ITEMS[iid]
            label = f"✔ {e} {n}" if eq else f"{e} {n}"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"axshop:eq:{iid}:{uid}")])
    rows.append([InlineKeyboardButton(text="🚫 Remove title", callback_data=f"axshop:un:none:{uid}")])
    text = (
        "🎀 <b>Your Titles</b>\n\n<blockquote>Tap a title to wear it. "
        "It shows on your /profile and in /rankings.</blockquote>"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _buy(pool: asyncpg.Pool, uid: int, iid: str) -> str:
    if iid not in ITEMS:
        return "Unknown item."
    e, n, price, cur = ITEMS[iid]
    col = "cash" if cur == "cash" else "gems"
    async with pool.acquire() as c:
        async with c.transaction():
            w = await c.fetchrow("SELECT cash, gems FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
            if w is None:
                return "Send /start to Rumi first 💜"
            if await c.fetchval(
                "SELECT 1 FROM ax_shop_owned WHERE user_id=$1 AND item_id=$2",
                uid,
                iid,
            ):
                return "You already own this ✅"
            before = w[col]
            if before < price:
                return "Not enough yet 💔 keep earning."
            await c.execute(
                f"UPDATE wallets SET {col}={col}-$1, updated_at=now() WHERE user_id=$2",
                price,
                uid,
            )
            await c.execute(
                "INSERT INTO transactions (user_id,type,currency,amount,balance_before,balance_after,source) "
                "VALUES ($1,'debit',$2,$3,$4,$5,'shop_title')",
                uid,
                col,
                price,
                before,
                before - price,
            )
            await c.execute("INSERT INTO ax_shop_owned (user_id,item_id) VALUES ($1,$2)", uid, iid)
    return f"Bought {e} {n} 💜 Use /titles to wear it."


async def _equip(pool: asyncpg.Pool, uid: int, iid: str) -> None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("UPDATE ax_shop_owned SET equipped=FALSE WHERE user_id=$1", uid)
            if iid != "none":
                await c.execute(
                    "UPDATE ax_shop_owned SET equipped=TRUE WHERE user_id=$1 AND item_id=$2",
                    uid,
                    iid,
                )


@router.message(Command("shop"))
async def shop_cmd(message: Message, pool: asyncpg.Pool) -> None:
    u = message.from_user
    if u is None:
        return
    if await pool.fetchrow("SELECT 1 FROM wallets WHERE user_id=$1", u.id) is None:
        await message.reply("Send /start to Rumi first 💜")
        return
    text, kb = await _shop_view(pool, u.id, u.first_name or "Player")
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.message(Command("titles"))
async def titles_cmd(message: Message, pool: asyncpg.Pool) -> None:
    u = message.from_user
    if u is None:
        return
    text, kb = await _titles_view(pool, u.id, u.first_name or "Player")
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.callback_query(F.data.startswith("axshop:"))
async def shop_cb(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await query.answer()
        return
    _, act, iid, uid_str = parts
    uid = int(uid_str)
    if query.from_user.id != uid:
        await query.answer("This menu belongs to someone else 💜", show_alert=True)
        return
    name = query.from_user.first_name or "Player"
    if act == "buy":
        await query.answer(await _buy(pool, uid, iid), show_alert=True)
        text, kb = await _shop_view(pool, uid, name)
    elif act in ("eq", "un"):
        await _equip(pool, uid, iid)
        await query.answer("Title updated 💜")
        text, kb = await _titles_view(pool, uid, name)
    else:
        await query.answer()
        return
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=H, reply_markup=kb)
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

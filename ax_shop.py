"""Title shop: buy titles with cash or gems, equip one, shown on profile and rankings (aiogram 3.x, bot_id aware)."""
import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import bot_context
import config
import ui
from bot_context import BotContext, get_brand

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


def item_display(iid: str, bot_id: int | None = None) -> tuple[str, str]:
    brand = get_brand(bot_id)
    emo, title, _, _ = ITEMS[iid]
    if iid == "chosen" and brand.name.lower() != "rumi":
        return emo, f"{brand.name}'s Chosen"
    return emo, title


def price_txt(p: int, cur: str) -> str:
    return f"{config.CURRENCY_SYMBOL}{p:,}" if cur == "cash" else f"{p:,} gems"


async def get_title(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None) -> str | None:
    bid = bot_context.resolve_bot_id(bot_id)
    row = await pool.fetchrow(
        "SELECT item_id FROM ax_shop_owned WHERE bot_id=$1 AND user_id=$2 AND equipped",
        bid,
        user_id,
    )
    if row and row["item_id"] in ITEMS:
        e, n = item_display(row["item_id"], bot_id=bid)
        return f"{e} {n}"
    return None


async def _owned(pool: asyncpg.Pool, uid: int, bot_id: int | None = None) -> dict[str, bool]:
    bid = bot_context.resolve_bot_id(bot_id)
    rows = await pool.fetch(
        "SELECT item_id, equipped FROM ax_shop_owned WHERE bot_id=$1 AND user_id=$2",
        bid,
        uid,
    )
    return {r["item_id"]: r["equipped"] for r in rows}


async def _shop_view(
    pool: asyncpg.Pool,
    uid: int,
    name: str,
    bot_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    bid = bot_context.resolve_bot_id(bot_id)
    brand = get_brand(bid)
    owned = await _owned(pool, uid, bot_id=bid)
    w = await pool.fetchrow(
        "SELECT cash, gems FROM wallets WHERE bot_id=$1 AND user_id=$2",
        bid,
        uid,
    )
    cash = w["cash"] if w else 0
    gems = w["gems"] if w else 0
    body, btns = [], []
    for iid, (_, _, p, c) in ITEMS.items():
        e, n = item_display(iid, bot_id=bid)
        body.append(f"{e} <b>{n}</b> · " + ("✅ owned" if iid in owned else price_txt(p, c)))
        if iid not in owned:
            btns.append(InlineKeyboardButton(text=f"{e} {n}", callback_data=f"axshop:buy:{iid}:{uid}"))
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    text = (
        f"🛍 <b>{ui.escape_html(brand.name)} Title Shop</b>\n\n<b>{ui.escape_html(name)}</b>, pick a title to show off 💜\n\n"
        f"<blockquote>{chr(10).join(body)}</blockquote>\n"
        f"You have <code>{config.CURRENCY_SYMBOL}{cash:,}</code> and <code>{gems:,}</code> gems\n"
        f"Use /titles to equip what you own."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _titles_view(
    pool: asyncpg.Pool,
    uid: int,
    name: str,
    bot_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    bid = bot_context.resolve_bot_id(bot_id)
    owned = await _owned(pool, uid, bot_id=bid)
    if not owned:
        return (
            f"🎀 <b>{ui.escape_html(name)}</b>, you own no titles yet 💜\n"
            f"Visit /shop to get your first one."
        ), None
    rows = []
    for iid, eq in owned.items():
        if iid in ITEMS:
            e, n = item_display(iid, bot_id=bid)
            label = f"✔ {e} {n}" if eq else f"{e} {n}"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"axshop:eq:{iid}:{uid}")])
    rows.append([InlineKeyboardButton(text="🚫 Remove title", callback_data=f"axshop:un:none:{uid}")])
    text = (
        "🎀 <b>Your Titles</b>\n\n<blockquote>Tap a title to wear it. "
        "It shows on your /profile and in /rankings.</blockquote>"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _buy(pool: asyncpg.Pool, uid: int, iid: str, bot_id: int | None = None) -> str:
    if iid not in ITEMS:
        return "Unknown item."
    bid = bot_context.resolve_bot_id(bot_id)
    brand = get_brand(bid)
    e, n = item_display(iid, bot_id=bid)
    _, _, price, cur = ITEMS[iid]
    col = "cash" if cur == "cash" else "gems"
    async with pool.acquire() as c:
        async with c.transaction():
            w = await c.fetchrow(
                "SELECT cash, gems FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bid,
                uid,
            )
            if w is None:
                return f"Send /start to {brand.name} first 💜"
            if await c.fetchval(
                "SELECT 1 FROM ax_shop_owned WHERE bot_id=$1 AND user_id=$2 AND item_id=$3",
                bid,
                uid,
                iid,
            ):
                return "You already own this ✅"
            before = w[col]
            if before < price:
                return "Not enough yet 💔 keep earning."
            await c.execute(
                f"UPDATE wallets SET {col}={col}-$1, updated_at=now() WHERE bot_id=$2 AND user_id=$3",
                price,
                bid,
                uid,
            )
            await c.execute(
                "INSERT INTO transactions (bot_id,user_id,type,currency,amount,balance_before,balance_after,source) "
                "VALUES ($1,$2,'debit',$3,$4,$5,$6,'shop_title')",
                bid,
                uid,
                col,
                price,
                before,
                before - price,
            )
            await c.execute(
                "INSERT INTO ax_shop_owned (bot_id,user_id,item_id) VALUES ($1,$2,$3)",
                bid,
                uid,
                iid,
            )
    return f"Bought {e} {n} 💜 Use /titles to wear it."


async def _equip(pool: asyncpg.Pool, uid: int, iid: str, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "UPDATE ax_shop_owned SET equipped=FALSE WHERE bot_id=$1 AND user_id=$2",
                bid,
                uid,
            )
            if iid != "none":
                await c.execute(
                    "UPDATE ax_shop_owned SET equipped=TRUE WHERE bot_id=$1 AND user_id=$2 AND item_id=$3",
                    bid,
                    uid,
                    iid,
                )


@router.message(Command("shop"))
async def shop_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    u = message.from_user
    if u is None:
        return
    bid = bot_ctx.bot_id
    brand = get_brand(bid)
    if await pool.fetchrow("SELECT 1 FROM wallets WHERE bot_id=$1 AND user_id=$2", bid, u.id) is None:
        await message.reply(f"Send /start to {brand.name} first 💜")
        return
    text, kb = await _shop_view(pool, u.id, u.first_name or "Player", bot_id=bid)
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.message(Command("titles"))
async def titles_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    u = message.from_user
    if u is None:
        return
    text, kb = await _titles_view(pool, u.id, u.first_name or "Player", bot_id=bot_ctx.bot_id)
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.callback_query(F.data.startswith("axshop:"))
async def shop_cb(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await query.answer()
        return
    _, act, iid, uid_str = parts
    uid = int(uid_str)
    if query.from_user.id != uid:
        await query.answer("This menu belongs to someone else 💜", show_alert=True)
        return
    bid = bot_ctx.bot_id
    name = query.from_user.first_name or "Player"
    if act == "buy":
        await query.answer(await _buy(pool, uid, iid, bot_id=bid), show_alert=True)
        text, kb = await _shop_view(pool, uid, name, bot_id=bid)
    elif act in ("eq", "un"):
        await _equip(pool, uid, iid, bot_id=bid)
        await query.answer("Title updated 💜")
        text, kb = await _titles_view(pool, uid, name, bot_id=bid)
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

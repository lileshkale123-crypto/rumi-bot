"""/toprich: global and group top 10 richest, with toggle button (aiogram 3.x, bot_id aware)."""
import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions, Message

import bot_context
import config
from ax_common import btn, esc
from bot_context import BotContext

router = Router(name="ax_toprich")

H = ParseMode.HTML
Q_GLOBAL = (
    "SELECT u.id, COALESCE(u.first_name,'Player') AS name, w.cash, "
    "p.emoji_id, p.emoji_char FROM wallets w "
    "JOIN users u ON u.id=w.user_id AND u.bot_id=w.bot_id "
    "LEFT JOIN ax_premium p ON p.user_id=u.id AND p.bot_id=w.bot_id "
    "WHERE w.bot_id=$1 ORDER BY w.cash DESC LIMIT 10"
)
Q_GROUP = (
    "SELECT u.id, COALESCE(u.first_name,'Player') AS name, w.cash, "
    "p.emoji_id, p.emoji_char FROM ax_chat_members m "
    "JOIN users u ON u.id=m.user_id AND u.bot_id=m.bot_id "
    "JOIN wallets w ON w.user_id=u.id AND w.bot_id=m.bot_id "
    "LEFT JOIN ax_premium p ON p.user_id=u.id AND p.bot_id=m.bot_id "
    "WHERE m.bot_id=$1 AND m.chat_id=$2 ORDER BY w.cash DESC LIMIT 10"
)


def _emoji(r) -> str:
    if r["emoji_char"]:
        ch = esc(r["emoji_char"])
        if r["emoji_id"]:
            return '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (r["emoji_id"], ch)
        return ch
    return "\U0001f464"


async def _card(
    pool: asyncpg.Pool,
    mode: str,
    chat_id: int,
    is_group: bool,
    bot_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    bid = bot_context.resolve_bot_id(bot_id)
    import ax_premium

    await ax_premium.ensure(pool)
    async with pool.acquire() as c:
        if mode == "g":
            rows = await c.fetch(Q_GROUP, bid, chat_id)
        else:
            rows = await c.fetch(Q_GLOBAL, bid)

    sym = getattr(config, "CURRENCY_SYMBOL", "$")
    word = "Group" if mode == "g" else "Global"
    lines = ["\U0001f3c6 <b>%s top 10 richest:</b>" % word, ""]
    for r in rows:
        link = '<a href="tg://user?id=%s">%s</a>' % (r["id"], esc(r["name"]))
        lines.append("%s %s: %s%s" % (_emoji(r), link, sym, r["cash"]))
    lines += ["", "\U0001f497 Use /premium to get a name emoji"]

    kb = None
    if is_group:
        if mode == "g":
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("\U0001f30d Global top 10", "axtr:w")]])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("\U0001f465 Group top 10", "axtr:g")]])
    return "\n".join(lines), kb


@router.message(Command("toprich"))
async def toprich(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    chat = message.chat
    is_group = chat.type in ("group", "supergroup")
    text, kb = await _card(pool, "w", chat.id, is_group, bot_id=bot_ctx.bot_id)
    await message.reply(
        text,
        parse_mode=H,
        reply_markup=kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.callback_query(F.data.regexp(r"^axtr:(w|g)$"))
async def toggle(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if query.message is None or not query.data:
        await query.answer()
        return
    mode = query.data.split(":")[1]
    chat = query.message.chat
    text, kb = await _card(pool, mode, chat.id, True, bot_id=bot_ctx.bot_id)
    try:
        await query.message.edit_text(
            text,
            parse_mode=H,
            reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception:
        pass
    await query.answer()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

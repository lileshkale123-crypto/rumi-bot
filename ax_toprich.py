"""/toprich: global and group top 10 richest, with toggle button."""
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

import config
from ax_common import btn, esc

H = ParseMode.HTML
Q_GLOBAL = (
    "SELECT u.id, COALESCE(u.first_name,'Player') AS name, w.cash, "
    "p.emoji_id, p.emoji_char FROM wallets w JOIN users u ON u.id=w.user_id "
    "LEFT JOIN ax_premium p ON p.user_id=u.id "
    "ORDER BY w.cash DESC LIMIT 10"
)
Q_GROUP = (
    "SELECT u.id, COALESCE(u.first_name,'Player') AS name, w.cash, "
    "p.emoji_id, p.emoji_char FROM ax_chat_members m "
    "JOIN users u ON u.id=m.user_id JOIN wallets w ON w.user_id=u.id "
    "LEFT JOIN ax_premium p ON p.user_id=u.id "
    "WHERE m.chat_id=$1 ORDER BY w.cash DESC LIMIT 10"
)


def _emoji(r):
    if r["emoji_char"]:
        ch = esc(r["emoji_char"])
        if r["emoji_id"]:
            return '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (r["emoji_id"], ch)
        return ch
    return "\U0001f464"


async def _card(pool, mode, chat_id, is_group):
    import ax_premium
    await ax_premium.ensure(pool)
    async with pool.acquire() as c:
        if mode == "g":
            rows = await c.fetch(Q_GROUP, chat_id)
        else:
            rows = await c.fetch(Q_GLOBAL)
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
            kb = [[btn("\U0001f30d Global top 10", "axtr:w")]]
        else:
            kb = [[btn("\U0001f465 Group top 10", "axtr:g")]]
    return "\n".join(lines), kb


async def toprich(update, context):
    chat = update.effective_chat
    is_group = chat.type in ("group", "supergroup")
    text, kb = await _card(context.bot_data["pool"], "w", chat.id, is_group)
    from telegram import InlineKeyboardMarkup
    mk = InlineKeyboardMarkup(kb) if kb else None
    await update.effective_message.reply_text(
        text, parse_mode=H, reply_markup=mk, disable_web_page_preview=True)


async def toggle(update, context):
    q = update.callback_query
    mode = q.data.split(":")[1]
    chat = q.message.chat
    text, kb = await _card(context.bot_data["pool"], mode, chat.id, True)
    from telegram import InlineKeyboardMarkup
    try:
        await q.edit_message_text(
            text, parse_mode=H, reply_markup=InlineKeyboardMarkup(kb),
            disable_web_page_preview=True)
    except Exception:
        pass
    await q.answer()


def register(app):
    app.add_handler(CommandHandler("toprich", toprich))
    app.add_handler(CallbackQueryHandler(toggle, pattern=r"^axtr:"))

"""Owner-only group view: info, tracked members, invite link. /group <chat_id>
Opened from the buttons in /adminpanel. Silent for non-owners."""
import html

from telegram.constants import ParseMode
from telegram import InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

import config
from ax_adminpanel import OWNER_IDS, _fmt
from ax_common import btn

PAGE = 10
SYMBOL = getattr(config, "CURRENCY_SYMBOL", "$")


async def view(context, chat_id, page):
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        g = await c.fetchrow("SELECT title FROM ax_groups WHERE chat_id=$1", chat_id)
        if g is None:
            return None, None
        agg = await c.fetchrow(
            "SELECT COUNT(*) AS n, COALESCE(SUM(w.cash),0)::bigint AS t "
            "FROM ax_chat_members m JOIN wallets w ON w.user_id = m.user_id "
            "WHERE m.chat_id=$1", chat_id)
        pages = max(1, -(-agg["n"] // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT u.id, COALESCE(u.first_name, u.username, 'Player') AS name, "
            "w.cash, w.level FROM ax_chat_members m "
            "JOIN users u ON u.id = m.user_id JOIN wallets w ON w.user_id = m.user_id "
            "WHERE m.chat_id=$1 ORDER BY w.cash DESC, u.id LIMIT $2 OFFSET $3",
            chat_id, PAGE, (page - 1) * PAGE)
    real = None
    link = None
    try:
        real = await context.bot.get_chat_member_count(chat_id)
    except Exception:
        pass
    try:
        ch = await context.bot.get_chat(chat_id)
        if ch.username:
            link = f"https://t.me/{ch.username}"
    except Exception:
        pass
    lines = []
    for i, r in enumerate(rows):
        rank = (page - 1) * PAGE + i + 1
        lines.append(
            f"<code>#{rank:02d}</code> <b>{html.escape(r['name'])}</b> "
            f"<code>{SYMBOL}{_fmt(r['cash'])}</code> • LVL {r['level']}\n"
            f"        <code>{r['id']}</code>")
    body = "\n".join(lines) if lines else "No tracked members yet."
    real_line = f"\n🌐 <b>In Telegram:</b> <code>{real}</code>" if real is not None else ""
    text = (
        f"🏰 <b>{html.escape(g['title'] or 'Group')}</b>\n"
        f"<blockquote>🆔 <code>{chat_id}</code>\n"
        f"👥 <b>Tracked:</b> <code>{agg['n']}</code>{real_line}\n"
        f"💰 <b>Total cash:</b> <code>{SYMBOL}{_fmt(agg['t'])}</code></blockquote>\n\n"
        f"<blockquote>{body}</blockquote>\n\n"
        f"<i>Page {page}/{pages}</i>")
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axg:v:{chat_id}:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axg:v:{chat_id}:{page + 1}"))
    link_btn = btn("🔗 Open group", url=link) if link else btn("🔗 Make invite link", f"axg:link:{chat_id}")
    kb = InlineKeyboardMarkup([nav, [link_btn], [btn("🕵️ Watch & read", f"axwz:r:{chat_id}")], [btn("🔊 Mute / Unmute fun", f"axwz:m:{chat_id}")], [btn("⬅ Back to panel", "axp:cash:1")]])
    return text, kb


async def group_cmd(update, context):
    user = update.effective_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = context.args or []
    try:
        chat_id = int(args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /group chat_id")
        return
    text, kb = await view(context, chat_id, 1)
    if text is None:
        await update.effective_message.reply_text("Rumi has no record of that group.")
        return
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def group_callback(update, context):
    q = update.callback_query
    if q.from_user.id not in OWNER_IDS:
        await q.answer()
        return
    parts = q.data.split(":")
    chat_id = int(parts[2])
    if parts[1] == "link":
        try:
            inv = await context.bot.create_chat_invite_link(chat_id, name="Rumi owner")
        except Exception as e:
            await q.answer(f"Couldn't: {str(e)[:150]}", show_alert=True)
            return
        await q.answer()
        await q.message.reply_text(f"🔗 {inv.invite_link}")
        return
    text, kb = await view(context, chat_id, int(parts[3]))
    if text is None:
        await q.answer("Group not found", show_alert=True)
        return
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass
    await q.answer()


def register(app):
    app.add_handler(CommandHandler("group", group_cmd))
    app.add_handler(CallbackQueryHandler(group_callback, pattern="^axg:"))

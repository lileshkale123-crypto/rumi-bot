"""Owner-only watchlist: saves recent messages of watched groups for 48h."""
import html

from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

from ax_adminpanel import OWNER_IDS
from ax_common import btn

SHOW = 20


async def _ids(context):
    ids = context.bot_data.get("watch_ids")
    if ids is None:
        async with context.bot_data["pool"].acquire() as c:
            rows = await c.fetch("SELECT chat_id FROM ax_watchlist")
        ids = {r["chat_id"] for r in rows}
        context.bot_data["watch_ids"] = ids
    return ids


async def logger(update, context):
    m = update.effective_message
    u = update.effective_user
    chat = update.effective_chat
    if m is None or u is None or chat is None:
        return
    if chat.id not in await _ids(context):
        return
    body = (m.text or m.caption or "[media]")[:500]
    async with context.bot_data["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO ax_watch_log (chat_id, message_id, user_id, name, body) "
            "VALUES ($1,$2,$3,$4,$5)",
            chat.id, m.message_id, u.id, u.first_name, body)
    title = chat.title or "Group"
    tag = f"[WATCH] {html.escape(title)}\n{html.escape(u.first_name or 'Member')}: {html.escape(body)}"
    for owner in OWNER_IDS:
        try:
            await context.bot.send_message(owner, tag)
        except Exception:
            pass


MAX_WATCH = 3


async def _set(context, chat_id, on):
    ids = await _ids(context)
    if on and chat_id not in ids and len(ids) >= MAX_WATCH:
        return False
    async with context.bot_data["pool"].acquire() as c:
        if on:
            await c.execute(
                "INSERT INTO ax_watchlist (chat_id) VALUES ($1) ON CONFLICT DO NOTHING", chat_id)
        else:
            await c.execute("DELETE FROM ax_watchlist WHERE chat_id=$1", chat_id)
    if on:
        ids.add(chat_id)
    else:
        ids.discard(chat_id)
    return True


PER = 10


async def read_view(context, chat_id, page=1):
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_watch_log WHERE created_at < now() - interval '7 days'")
        title = await c.fetchval("SELECT title FROM ax_groups WHERE chat_id=$1", chat_id)
        total = await c.fetchval("SELECT COUNT(*) FROM ax_watch_log WHERE chat_id=$1", chat_id)
        pages = max(1, -(-total // PER))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT name, body, created_at FROM ax_watch_log "
            "WHERE chat_id=$1 ORDER BY id DESC LIMIT $2 OFFSET $3",
            chat_id, PER, (page - 1) * PER)
    watched = chat_id in await _ids(context)
    lines = []
    for r in reversed(rows):
        t = r["created_at"].strftime("%d %b %H:%M")
        who = html.escape(r["name"] or "Member")
        txt = html.escape((r["body"] or "")[:160])
        lines.append(f"<code>{t}</code> <b>{who}</b>: {txt}")
    body = "\n".join(lines) if lines else "No saved messages yet."
    state = "watching" if watched else "not watching"
    text = (f"<b>{html.escape(title or 'Group')}</b>\n"
            f"<blockquote><code>{chat_id}</code> | {state} | {total} saved | kept 7 days</blockquote>\n\n"
            f"<blockquote>{body}</blockquote>\n"
            f"<i>Page {page}/{pages} (page 1 = newest)</i>")
    nav = []
    if page > 1:
        nav.append(btn("Newer", f"axwz:p:{chat_id}:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("Older", f"axwz:p:{chat_id}:{page + 1}"))
    act = "off" if watched else "on"
    label = "Stop watching" if watched else "Start watching"
    kb = InlineKeyboardMarkup([
        nav,
        [btn("Refresh", f"axwz:p:{chat_id}:1"), btn(label, f"axwz:{act}:{chat_id}")],
        [btn("Back to group", f"axg:v:{chat_id}:1")],
    ])
    return text, kb


async def _cmd(update, context, on):
    u = update.effective_user
    if u is None or u.id not in OWNER_IDS:
        return
    try:
        chat_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /watch chat_id  •  /unwatch chat_id  •  /readlog chat_id")
        return
    if on is not None:
        await _set(context, chat_id, on)
    text, kb = await read_view(context, chat_id)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def watch_cmd(update, context):
    await _cmd(update, context, True)


async def unwatch_cmd(update, context):
    await _cmd(update, context, False)


async def readlog_cmd(update, context):
    await _cmd(update, context, None)


async def watch_callback(update, context):
    q = update.callback_query
    if q.from_user.id not in OWNER_IDS:
        await q.answer()
        return
    parts = q.data.split(":")
    act = parts[1]
    chat_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 1
    if act in ("on", "off"):
        await _set(context, chat_id, act == "on")
    text, kb = await read_view(context, chat_id, page)
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass
    await q.answer()


def register(app):
    app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.PRIVATE, logger), group=-7)
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("unwatch", unwatch_cmd))
    app.add_handler(CommandHandler("readlog", readlog_cmd))
    app.add_handler(CallbackQueryHandler(watch_callback, pattern="^axwz:"))

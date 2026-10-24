"""Owner-only broadcast: copy any message to groups or DMs, with confirm and delete."""
import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

from ax_adminpanel import OWNER_IDS

H = ParseMode.HTML
_READY = [False]


async def ensure(pool):
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_bc_batches ("
            "id BIGSERIAL PRIMARY KEY, kind TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_bc_msgs ("
            "batch_id BIGINT NOT NULL, chat_id BIGINT NOT NULL, "
            "message_id BIGINT NOT NULL)")
    _READY[0] = True


def _kb(*pairs):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in pairs]])


async def _start(update, context, kind):
    user = update.effective_user
    if user is None or user.id not in OWNER_IDS:
        return
    m = update.effective_message
    src = m.reply_to_message
    if src is None:
        await m.reply_text("Reply to the message you want to broadcast, then send the command.")
        return
    pool = context.bot_data["pool"]
    sql = "SELECT count(*) FROM ax_groups" if kind == "g" else "SELECT count(*) FROM users"
    async with pool.acquire() as c:
        n = await c.fetchval(sql)
    where = "groups" if kind == "g" else "DM users"
    p = await src.reply_text(
        "📢 <b>Broadcast preview</b>\n<blockquote>Send this message to %d %s?</blockquote>" % (n, where),
        parse_mode=H, reply_markup=_kb(("✅ Confirm", "axb:ok"), ("❌ Cancel", "axb:no")))
    context.bot_data.setdefault("axb_pending", {})[p.message_id] = (src.chat_id, src.message_id, kind)


async def broadcast_cmd(update, context):
    await _start(update, context, "g")


async def dmbroadcast_cmd(update, context):
    await _start(update, context, "d")


async def _ok(update, context):
    q = update.callback_query
    if q.from_user.id not in OWNER_IDS:
        await q.answer("Owner only", show_alert=True)
        return
    pend = context.bot_data.get("axb_pending", {}).pop(q.message.message_id, None)
    if pend is None:
        await q.answer("Expired. Send the command again.", show_alert=True)
        return
    await q.answer("Sending...")
    src_chat, src_msg, kind = pend
    pool = context.bot_data["pool"]
    await ensure(pool)
    sql = "SELECT chat_id FROM ax_groups" if kind == "g" else "SELECT id AS chat_id FROM users"
    async with pool.acquire() as c:
        rows = await c.fetch(sql)
        bid = await c.fetchval("INSERT INTO ax_bc_batches (kind) VALUES ($1) RETURNING id", kind)
    await q.edit_message_text("📢 Sending to %d chats..." % len(rows))
    sent, failed, saved = 0, 0, []
    for r in rows:
        try:
            if kind == "g":
                _ch = await context.bot.get_chat(r["chat_id"])
                if _ch.type == "channel":
                    continue
            msg = await context.bot.copy_message(
                chat_id=r["chat_id"], from_chat_id=src_chat, message_id=src_msg)
            saved.append((bid, r["chat_id"], msg.message_id))
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    if saved:
        async with pool.acquire() as c:
            await c.executemany(
                "INSERT INTO ax_bc_msgs (batch_id, chat_id, message_id) VALUES ($1,$2,$3)", saved)
    await q.edit_message_text(
        "📢 <b>Broadcast done</b>\n<blockquote>Sent: %d\nFailed: %d\nTotal: %d</blockquote>"
        % (sent, failed, len(rows)),
        parse_mode=H, reply_markup=_kb(("🗑 Delete broadcast", "axb:del:%d" % bid)))


async def _no(update, context):
    q = update.callback_query
    if q.from_user.id not in OWNER_IDS:
        await q.answer("Owner only", show_alert=True)
        return
    context.bot_data.get("axb_pending", {}).pop(q.message.message_id, None)
    await q.answer()
    await q.edit_message_text("❌ Broadcast cancelled. Nothing was sent.")


async def _del(update, context):
    q = update.callback_query
    if q.from_user.id not in OWNER_IDS:
        await q.answer("Owner only", show_alert=True)
        return
    bid = int(q.data.split(":")[2])
    await q.answer("Deleting...")
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT chat_id, message_id FROM ax_bc_msgs WHERE batch_id=$1", bid)
    ok = 0
    for r in rows:
        try:
            await context.bot.delete_message(r["chat_id"], r["message_id"])
            ok += 1
        except Exception:
            logging.info("broadcast delete failed for %s", r["chat_id"])
        await asyncio.sleep(0.05)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_bc_msgs WHERE batch_id=$1", bid)
    await q.edit_message_text("🗑 <b>Broadcast deleted</b>\n<blockquote>Removed from %d of %d chats</blockquote>" % (ok, len(rows)), parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("dmbroadcast", dmbroadcast_cmd))
    app.add_handler(CallbackQueryHandler(_ok, pattern=r"^axb:ok$"))
    app.add_handler(CallbackQueryHandler(_no, pattern=r"^axb:no$"))
    app.add_handler(CallbackQueryHandler(_del, pattern=r"^axb:del:"))

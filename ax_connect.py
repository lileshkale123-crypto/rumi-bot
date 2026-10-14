"""Connections: /connect /disconnect /reconnect /connection (use in DM)."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

from ax_common import esc, is_admin

H = ParseMode.HTML


async def _say(update, text, **kw):
    return await update.effective_message.reply_text(
        text, parse_mode=H, **kw)


async def _dm_only(update):
    if update.effective_chat.type != "private":
        await _say(update, "📩 <b>Use it in private</b>\n<blockquote>Send this to me in a private chat ✨</blockquote>")
        return False
    return True


async def _title(bot, chat_id):
    try:
        return (await bot.get_chat(chat_id)).title or str(chat_id)
    except Exception:
        return str(chat_id)


async def _link(context, user, chat_id):
    async with context.bot_data["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO ax_connections (user_id, chat_id, last_chat_id) "
            "VALUES ($1,$2,$2) ON CONFLICT (user_id) DO UPDATE "
            "SET chat_id=$2, last_chat_id=$2", user.id, chat_id)


async def connect_cmd(update, context):
    if not await _dm_only(update):
        return
    user = update.effective_user
    if not context.args:
        await _say(update, "🔗 <b>Connect to a group</b>\n<blockquote>Send /connect followed by the group id.\nExample: /connect -1001234567890\nTip: send /id inside the group to see its id.</blockquote>")
        return
    try:
        cid = int(context.args[0])
    except ValueError:
        await _say(update, "⚠️ <b>That is not a group id</b>\n<blockquote>It looks like a number starting with -100 ✨</blockquote>")
        return
    if not await is_admin(context.bot, cid, user.id):
        await _say(update, "🔒 <b>Admins only</b>\n<blockquote>You must be an admin of that group, and I must be inside it ✨</blockquote>")
        return
    await _link(context, user, cid)
    name = await _title(context.bot, cid)
    await _say(update, "✅ <b>Connected</b>\n<blockquote>%s, you are linked to %s 💫</blockquote>" % (esc(user.first_name or "friend"), esc(name)))


async def connection_cmd(update, context):
    if not await _dm_only(update):
        return
    async with context.bot_data["pool"].acquire() as c:
        r = await c.fetchrow(
            "SELECT chat_id FROM ax_connections WHERE user_id=$1",
            update.effective_user.id)
    if not r or not r["chat_id"]:
        await _say(update, "🔌 <b>Not connected</b>\n<blockquote>Use /connect with a group id to link one ✨</blockquote>")
        return
    name = await _title(context.bot, r["chat_id"])
    await _say(update, "🔗 <b>Connected to</b>\n<blockquote>%s</blockquote>" % esc(name))


async def disconnect_cmd(update, context):
    if not await _dm_only(update):
        return
    async with context.bot_data["pool"].acquire() as c:
        await c.execute(
            "UPDATE ax_connections SET chat_id=0 WHERE user_id=$1 AND chat_id<>0",
            update.effective_user.id)
    await _say(update, "✅ <b>Disconnected</b>\n<blockquote>Use /reconnect to link your last group again 💫</blockquote>")


async def reconnect_cmd(update, context):
    if not await _dm_only(update):
        return
    async with context.bot_data["pool"].acquire() as c:
        r = await c.fetchrow(
            "SELECT last_chat_id FROM ax_connections WHERE user_id=$1",
            update.effective_user.id)
    if not r or not r["last_chat_id"]:
        await _say(update, "🔌 <b>No earlier group</b>\n<blockquote>Use /connect with a group id first ✨</blockquote>")
        return
    name = await _title(context.bot, r["last_chat_id"])
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "Connect to " + name[:30], callback_data="axcn_" + str(r["last_chat_id"]))]])
    await _say(update, "🔁 <b>Reconnect</b>\n<blockquote>Tap to link %s again 💫</blockquote>" % esc(name), reply_markup=kb)


async def reconnect_cb(update, context):
    q = update.callback_query
    try:
        cid = int(q.data[5:])
    except ValueError:
        await q.answer()
        return
    if not await is_admin(context.bot, cid, q.from_user.id):
        await q.answer("You are not an admin there", show_alert=True)
        return
    await _link(context, q.from_user, cid)
    await q.answer()
    await q.edit_message_text("✅ Connected again 💫")


def register(app):
    app.add_handler(CommandHandler("connect", connect_cmd))
    app.add_handler(CommandHandler("connection", connection_cmd))
    app.add_handler(CommandHandler("disconnect", disconnect_cmd))
    app.add_handler(CommandHandler("reconnect", reconnect_cmd))
    app.add_handler(CallbackQueryHandler(reconnect_cb, pattern="^axcn_"))

"""One-tap connect: group button -> DM deep link."""
from telegram.constants import ParseMode
from telegram import InlineKeyboardMarkup
from telegram.ext import (ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, filters)

from ax_common import btn, esc, is_admin
import ax_connect

H = ParseMode.HTML


async def group_connect(update, context):
    chat, user = update.effective_chat, update.effective_user
    msg = update.effective_message
    if not await is_admin(context.bot, chat.id, user.id):
        await msg.reply_text(
            "\U0001F512 <b>Admins only</b>\n<blockquote>Only admins can connect this group \u2728</blockquote>",
            parse_mode=H)
        raise ApplicationHandlerStop
    url = f"https://t.me/{context.bot.username}?start=connect_{chat.id}"
    kb = InlineKeyboardMarkup([[btn("\U0001F517 Manage in private", url=url, style="success")]])
    await msg.reply_text(
        "\U0001F517 <b>Connect this group</b>\n"
        "<blockquote>Tap the button to open a private chat with me, already linked to "
        f"<b>{esc(chat.title or 'this group')}</b> \u2728</blockquote>",
        parse_mode=H, reply_markup=kb)
    raise ApplicationHandlerStop


async def start_payload(update, context):
    if update.effective_chat.type != "private":
        return
    if not context.args or not context.args[0].startswith("connect_"):
        return
    user, msg = update.effective_user, update.effective_message
    try:
        cid = int(context.args[0][len("connect_"):])
    except ValueError:
        return
    if not await is_admin(context.bot, cid, user.id):
        await msg.reply_text(
            "\U0001F512 <b>Admins only</b>\n<blockquote>You need to be an admin of that group, "
            "and I must be inside it \u2728</blockquote>", parse_mode=H)
        raise ApplicationHandlerStop
    await ax_connect._link(context, user, cid)
    name = await ax_connect._title(context.bot, cid)
    await msg.reply_text(
        "\u2705 <b>Connected</b>\n<blockquote>%s, you are linked to <b>%s</b> \U0001F4AB\n"
        "Now you can send these here and they change your group:\n"
        "/settings /notes /save /filters /filter /rules /setrules /locks /warnings\n"
        "Use /connection to check, /disconnect to unlink.</blockquote>"
        % (esc(user.first_name or "friend"), esc(name)), parse_mode=H)
    raise ApplicationHandlerStop


async def dm_connect(update, context):
    if update.effective_chat.type != "private" or context.args:
        return
    user, msg = update.effective_user, update.effective_message
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT chat_id, title FROM ax_groups ORDER BY title LIMIT 60")
    found = []
    for r in rows:
        try:
            if await is_admin(context.bot, r["chat_id"], user.id):
                found.append(r)
        except Exception:
            pass
        if len(found) >= 10:
            break
    if not found:
        url = f"https://t.me/{context.bot.username}?startgroup=true"
        kb = InlineKeyboardMarkup([[btn("\u2795 Add Rumi to your group", url=url, style="success")]])
        await msg.reply_text(
            "\U0001F517 <b>No groups found</b>\n"
            "<blockquote>I could not find a group where you are an admin and I am present.\n"
            "Add me to your group, make me admin, then send /connect inside it. "
            "Or send /connect followed by the group id \u2728</blockquote>",
            parse_mode=H, reply_markup=kb)
        raise ApplicationHandlerStop
    kb = InlineKeyboardMarkup(
        [[btn("\U0001F465 " + (r["title"] or "Group")[:40], f"axcc:{r['chat_id']}", style="primary")] for r in found])
    await msg.reply_text(
        "\U0001F517 <b>Pick your group</b>\n"
        "<blockquote>Tap a group to manage it from here \u2728</blockquote>",
        parse_mode=H, reply_markup=kb)
    raise ApplicationHandlerStop


async def pick_cb(update, context):
    q = update.callback_query
    user = q.from_user
    try:
        cid = int(q.data.split(":", 1)[1])
    except ValueError:
        await q.answer()
        return
    if not await is_admin(context.bot, cid, user.id):
        await q.answer("You are not an admin there", show_alert=True)
        return
    await ax_connect._link(context, user, cid)
    name = await ax_connect._title(context.bot, cid)
    await q.answer("Connected \u2728")
    try:
        await q.edit_message_text(
            "\u2705 <b>Connected</b>\n<blockquote>%s, you are linked to <b>%s</b> \U0001F4AB\n"
            "Now you can send these here and they change your group:\n"
            "/settings /notes /save /filters /filter /rules /setrules /locks /warnings\n"
            "Use /connection to check, /disconnect to unlink.</blockquote>"
            % (esc(user.first_name or "friend"), esc(name)), parse_mode=H)
    except Exception:
        pass


def register(app):
    app.add_handler(CommandHandler("connect", group_connect, filters=filters.ChatType.GROUPS), group=-1)
    app.add_handler(CommandHandler("start", start_payload, filters=filters.ChatType.PRIVATE), group=-1)
    app.add_handler(CommandHandler("connect", dm_connect, filters=filters.ChatType.PRIVATE), group=-1)
    app.add_handler(CallbackQueryHandler(pick_cb, pattern="^axcc:"))


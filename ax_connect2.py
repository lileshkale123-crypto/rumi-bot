"""One-tap connect: group button -> DM deep link."""
from telegram.constants import ParseMode
from telegram import InlineKeyboardMarkup
from telegram.ext import (ApplicationHandlerStop, CommandHandler, filters)

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


def register(app):
    app.add_handler(CommandHandler("connect", group_connect, filters=filters.ChatType.GROUPS), group=-1)
    app.add_handler(CommandHandler("start", start_payload, filters=filters.ChatType.PRIVATE), group=-1)

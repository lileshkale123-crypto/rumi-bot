"""/adminlist /admincache /id"""
import logging

from telegram.constants import ChatMemberStatus
from telegram.ext import CommandHandler

import ax_mod
from ax_common import esc, resolve_target

log = logging.getLogger(__name__)


def _mention(user):
    return f'<a href="tg://user?id={user.id}">{esc(user.first_name or "Admin")}</a>'


async def _fetch_admins(bot, chat_id):
    return await bot.get_chat_administrators(chat_id)


async def adminlist_cmd(update, context):
    chat = update.effective_chat
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside a group 💫</blockquote>")
        return
    try:
        admins = await _fetch_admins(context.bot, chat.id)
    except Exception as e:
        await ax_mod.say(update, f"⚠️ <b>Couldn't load admins</b>\n<blockquote>{esc(str(e))}</blockquote>")
        return
    owner = [a for a in admins if a.status == ChatMemberStatus.OWNER]
    humans = [a for a in admins if a.status != ChatMemberStatus.OWNER and not a.user.is_bot]
    bots = [a for a in admins if a.user.is_bot]
    lines = []
    for a in owner:
        lines.append(f"👑 {_mention(a.user)}")
    for a in humans:
        title = f" • <i>{esc(a.custom_title)}</i>" if getattr(a, "custom_title", None) else ""
        lines.append(f"🛡️ {_mention(a.user)}{title}")
    for a in bots:
        lines.append(f"🤖 {esc(a.user.first_name)}")
    await ax_mod.say(update, (
        f"👮 <b>Admins of {esc(chat.title or 'this group')}</b>\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>"))


async def admincache_cmd(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside a group 💫</blockquote>")
        return
    try:
        admins = await _fetch_admins(context.bot, chat.id)
    except Exception as e:
        await ax_mod.say(update, f"⚠️ <b>Couldn't refresh</b>\n<blockquote>{esc(str(e))}</blockquote>")
        return
    if user.id not in {a.user.id for a in admins}:
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return
    await ax_mod.say(update, f"🔄 <b>Admin list refreshed</b>\n<blockquote>Found <b>{len(admins)}</b> admins right now ✨</blockquote>")


async def id_cmd(update, context):
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    target = None
    if msg.reply_to_message or context.args:
        target = await resolve_target(update, context, context.bot_data["pool"])
    who = target or user
    lines = [f"👤 <b>{esc(who.first_name or 'User')}:</b> <code>{who.id}</code>"]
    if chat.type != "private":
        lines.append(f"💬 <b>Group:</b> <code>{chat.id}</code>")
    fwd = getattr(msg.reply_to_message, "forward_origin", None) if msg.reply_to_message else None
    sender = getattr(fwd, "sender_user", None)
    if sender:
        lines.append(f"↪️ <b>Forwarded from {esc(sender.first_name)}:</b> <code>{sender.id}</code>")
    await ax_mod.say(update, f"🆔 <b>IDs</b>\n<blockquote>{chr(10).join(lines)}</blockquote>")


def register(app):
    app.add_handler(CommandHandler("adminlist", adminlist_cmd))
    app.add_handler(CommandHandler("admincache", admincache_cmd))
    app.add_handler(CommandHandler("id", id_cmd))

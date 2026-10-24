"""Purge tools: /purge /spurge /del /purgefrom /purgeto (admins only)."""
import asyncio
import logging

from ax_common import is_admin, bot_is_admin
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

import ax_mod

log = logging.getLogger(__name__)
MARKS = {}  # chat_id -> message_id marked by /purgefrom
MAX_PURGE = 1000


async def _check(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    if not await bot_is_admin(context.bot, chat.id):
        await ax_mod.say(update, "⚠️ <b>I need admin rights</b>\n<blockquote>Give me the delete messages permission 💫</blockquote>")
        return False
    return True


async def _delete_range(bot, chat_id, first, last):
    ids = list(range(first, last + 1))
    done = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            await bot.delete_messages(chat_id, chunk)
            done += len(chunk)
        except Exception as e:
            log.warning("purge chunk failed: %s", e)
        await asyncio.sleep(0.3)
    return done


async def _purge(update, context, silent):
    if not await _check(update, context):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id
    if not msg.reply_to_message:
        await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the first message you want gone.\nOr: /purge 20 as a reply to delete 20 messages from there.</blockquote>")
        return
    first = msg.reply_to_message.message_id
    last = msg.message_id
    args = context.args or []
    if args and args[0].isdigit():
        last = min(msg.message_id, first + int(args[0]) - 1)
    if last - first + 1 > MAX_PURGE:
        await ax_mod.say(update, f"⚠️ <b>Too many</b>\n<blockquote>I purge up to {MAX_PURGE} messages at once</blockquote>")
        return
    done = await _delete_range(context.bot, chat_id, first, msg.message_id if len(args) == 0 else last)
    if args and args[0].isdigit():
        try:
            await msg.delete()
        except Exception:
            pass
    if silent:
        return
    note = await context.bot.send_message(
        chat_id, f"🧹 <b>Purge complete</b>\n<blockquote>Cleared <b>{done}</b> messages ✨</blockquote>",
        parse_mode=ParseMode.HTML)
    await asyncio.sleep(5)
    try:
        await note.delete()
    except Exception:
        pass


async def purge_cmd(update, context):
    await _purge(update, context, False)


async def spurge_cmd(update, context):
    await _purge(update, context, True)


async def del_cmd(update, context):
    if not await _check(update, context):
        return
    msg = update.effective_message
    if not msg.reply_to_message:
        await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want removed</blockquote>")
        return
    for m in (msg.reply_to_message, msg):
        try:
            await m.delete()
        except Exception:
            pass


async def purgefrom_cmd(update, context):
    if not await _check(update, context):
        return
    msg = update.effective_message
    if not msg.reply_to_message:
        await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the message where the purge should start</blockquote>")
        return
    MARKS[update.effective_chat.id] = msg.reply_to_message.message_id
    await ax_mod.say(update, "📍 <b>Start marked</b>\n<blockquote>Now reply to the last message with /purgeto</blockquote>")


async def purgeto_cmd(update, context):
    if not await _check(update, context):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id
    start = MARKS.get(chat_id)
    if start is None:
        await ax_mod.say(update, "💫 <b>No start mark</b>\n<blockquote>Use /purgefrom on the first message first</blockquote>")
        return
    if not msg.reply_to_message:
        await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the last message you want deleted</blockquote>")
        return
    end = msg.reply_to_message.message_id
    if end < start or end - start + 1 > MAX_PURGE:
        await ax_mod.say(update, f"⚠️ <b>Bad range</b>\n<blockquote>The end must come after the start, within {MAX_PURGE} messages</blockquote>")
        return
    done = await _delete_range(context.bot, chat_id, start, end)
    MARKS.pop(chat_id, None)
    try:
        await msg.delete()
    except Exception:
        pass
    note = await context.bot.send_message(
        chat_id, f"🧹 <b>Purge complete</b>\n<blockquote>Cleared <b>{done}</b> messages ✨</blockquote>",
        parse_mode=ParseMode.HTML)
    await asyncio.sleep(5)
    try:
        await note.delete()
    except Exception:
        pass


def register(app):
    app.add_handler(CommandHandler("purge", purge_cmd))
    app.add_handler(CommandHandler("spurge", spurge_cmd))
    app.add_handler(CommandHandler("del", del_cmd))
    app.add_handler(CommandHandler("purgefrom", purgefrom_cmd))
    app.add_handler(CommandHandler("purgeto", purgeto_cmd))


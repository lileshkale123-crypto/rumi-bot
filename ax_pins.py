"""Pins: /pin /permapin /unpin /unpinall /pinned /antichannelpin /cleanlinked"""
import logging

from telegram.ext import CommandHandler, MessageHandler, filters

import ax_mod
from ax_common import esc, is_admin

log = logging.getLogger(__name__)


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


async def _settings(pool, chat_id):
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM ax_pin_settings WHERE chat_id=$1", chat_id)
    return dict(r) if r else {"antichannelpin": False, "cleanlinked": False}


async def pin_cmd(update, context):
    if not await _gate(update, context):
        return
    msg = update.effective_message
    if not msg.reply_to_message:
        await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the message to pin. Add loud to notify everyone: /pin loud</blockquote>")
        return
    loud = bool(context.args) and context.args[0].lower() in ("loud", "notify")
    try:
        await context.bot.pin_chat_message(update.effective_chat.id, msg.reply_to_message.message_id, disable_notification=not loud)
    except Exception as e:
        await ax_mod.say(update, f"⚠️ <b>Couldn't pin</b>\n<blockquote>{esc(str(e))}\nI need the Pin messages permission</blockquote>")
        return
    await ax_mod.say(update, "📌 <b>Pinned</b>\n<blockquote>Message pinned ✨</blockquote>")


async def permapin_cmd(update, context):
    if not await _gate(update, context):
        return
    parts = (update.effective_message.text or "").split(None, 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    if not body:
        await ax_mod.say(update, "💫 <b>Text?</b>\n<blockquote>Example: /permapin Meeting at 8pm</blockquote>")
        return
    try:
        sent = await context.bot.send_message(update.effective_chat.id, esc(body), parse_mode="HTML")
        await context.bot.pin_chat_message(update.effective_chat.id, sent.message_id, disable_notification=True)
    except Exception as e:
        await ax_mod.say(update, f"⚠️ <b>Couldn't pin</b>\n<blockquote>{esc(str(e))}</blockquote>")


async def unpin_cmd(update, context):
    if not await _gate(update, context):
        return
    msg = update.effective_message
    try:
        if msg.reply_to_message:
            await context.bot.unpin_chat_message(update.effective_chat.id, msg.reply_to_message.message_id)
        else:
            await context.bot.unpin_chat_message(update.effective_chat.id)
    except Exception as e:
        await ax_mod.say(update, f"⚠️ <b>Couldn't unpin</b>\n<blockquote>{esc(str(e))}</blockquote>")
        return
    await ax_mod.say(update, "📌 <b>Unpinned</b>\n<blockquote>Done ✨</blockquote>")


async def unpinall_cmd(update, context):
    if not await _gate(update, context):
        return
    try:
        await context.bot.unpin_all_chat_messages(update.effective_chat.id)
    except Exception as e:
        await ax_mod.say(update, f"⚠️ <b>Couldn't unpin</b>\n<blockquote>{esc(str(e))}</blockquote>")
        return
    await ax_mod.say(update, "📌 <b>All unpinned</b>\n<blockquote>Every pinned message was removed</blockquote>")


async def pinned_cmd(update, context):
    chat = update.effective_chat
    if chat.type == "private":
        return
    full = await context.bot.get_chat(chat.id)
    p = full.pinned_message
    if not p:
        await ax_mod.say(update, "📌 <b>Nothing pinned</b>\n<blockquote>No pinned message here</blockquote>")
        return
    await update.effective_message.reply_text("📌 Pinned message:", reply_to_message_id=p.message_id)


async def _toggle(update, context, col, label, detail):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    arg = (context.args or [""])[0].lower()
    if arg not in ("on", "off", "yes", "no"):
        s = await _settings(pool, chat_id)
        await ax_mod.say(update, f"⚙️ <b>{label}</b>\n<blockquote>Currently <b>{'on' if s[col] else 'off'}</b>\nUse on or off</blockquote>")
        return
    val = arg in ("on", "yes")
    assert col in ("antichannelpin", "cleanlinked")
    async with pool.acquire() as c:
        await c.execute(f"INSERT INTO ax_pin_settings (chat_id, {col}) VALUES ($1,$2) "
                        f"ON CONFLICT (chat_id) DO UPDATE SET {col}=$2", chat_id, val)
    await ax_mod.say(update, f"✅ <b>{label} {'on' if val else 'off'}</b>\n<blockquote>{detail}</blockquote>")


async def antichannelpin_cmd(update, context):
    await _toggle(update, context, "antichannelpin", "Anti channel pin", "Telegram's auto-pin of linked channel posts is undone")


async def cleanlinked_cmd(update, context):
    await _toggle(update, context, "cleanlinked", "Clean linked", "Messages from the linked channel are deleted")


async def linked_watch(update, context):
    msg = update.effective_message
    if not msg or not msg.is_automatic_forward:
        return
    pool, chat = context.bot_data["pool"], update.effective_chat
    s = await _settings(pool, chat.id)
    try:
        if s["antichannelpin"]:
            await context.bot.unpin_chat_message(chat.id, msg.message_id)
        if s["cleanlinked"]:
            await msg.delete()
    except Exception as e:
        log.warning("linked watch failed: %s", e)


def register(app):
    app.add_handler(CommandHandler("pin", pin_cmd))
    app.add_handler(CommandHandler("permapin", permapin_cmd))
    app.add_handler(CommandHandler("unpin", unpin_cmd))
    app.add_handler(CommandHandler("unpinall", unpinall_cmd))
    app.add_handler(CommandHandler("pinned", pinned_cmd))
    app.add_handler(CommandHandler("antichannelpin", antichannelpin_cmd))
    app.add_handler(CommandHandler("cleanlinked", cleanlinked_cmd))
    app.add_handler(MessageHandler(filters.IS_AUTOMATIC_FORWARD, linked_watch), group=-3)

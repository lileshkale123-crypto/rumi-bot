"""Extra ban/mute/kick variants: d* (delete msg), s* (silent), kickme."""
import logging

from telegram import InlineKeyboardMarkup
from telegram.ext import CommandHandler

import ax_mod
from ax_common import btn, is_admin

log = logging.getLogger(__name__)


async def _del(msg):
    try:
        await msg.delete()
    except Exception:
        pass


def make_variant(kind, mode):
    async def cmd(update, context):
        msg = update.effective_message
        if mode == "d" and not msg.reply_to_message:
            await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want removed</blockquote>")
            return
        target = await ax_mod.gate(update, context, kind in ("mute", "ban", "kick"))
        if target is None:
            return
        chat_id = update.effective_chat.id
        try:
            await ax_mod.perform(kind, context.bot, context.bot_data["pool"], chat_id, target.id)
        except Exception as e:
            await ax_mod.say(update, f"⚠️ <b>Couldn't do that</b>\n<blockquote>{ax_mod.esc(str(e))}</blockquote>")
            return
        if mode == "d":
            await _del(msg.reply_to_message)
            await _del(msg)
        elif mode == "s":
            await _del(msg)
            return
        icon, title = ax_mod.META[kind]
        kb = None
        if kind in ax_mod.UNDO:
            k, label = ax_mod.UNDO[kind]
            kb = InlineKeyboardMarkup([[btn(label, f"axm:{k}:{target.id}", style="success")]])
        await ax_mod.say(update, ax_mod.card(icon, title, target.first_name), kb)
    return cmd


async def kickme_cmd(update, context):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        await msg.reply_text("Use this inside a group 💜")
        return
    if await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins stay</b>\n<blockquote>I can't kick admins ✨</blockquote>")
        return
    try:
        await ax_mod.perform("kick", context.bot, context.bot_data["pool"], chat.id, user.id)
    except Exception as e:
        await ax_mod.say(update, f"⚠️ <b>Couldn't do that</b>\n<blockquote>{ax_mod.esc(str(e))}</blockquote>")
        return
    await ax_mod.say(update, f"👢 <b>{ax_mod.esc(user.first_name)}</b> kicked themselves out. Bye! 💜")


def register(app):
    for kind in ("ban", "mute", "kick"):
        app.add_handler(CommandHandler("d" + kind, make_variant(kind, "d")))
        app.add_handler(CommandHandler("s" + kind, make_variant(kind, "s")))
    app.add_handler(CommandHandler("kickme", kickme_cmd))


"""Reports: /report, @admin (as a reply) and /reports on|off"""
import re

from telegram.ext import CommandHandler, MessageHandler, filters

import ax_mod
from ax_common import esc, is_admin

_AT_ADMIN = re.compile(r"(?i)^\s*@admins?\b")


async def enabled(pool, chat_id):
    async with pool.acquire() as c:
        v = await c.fetchval("SELECT enabled FROM ax_report_settings WHERE chat_id=$1", chat_id)
    return True if v is None else v


async def _do_report(update, context):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if chat.type == "private" or not msg.reply_to_message:
        if chat.type != "private":
            await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want to report</blockquote>")
        return
    pool = context.bot_data["pool"]
    if not await enabled(pool, chat.id):
        return
    target = msg.reply_to_message.from_user
    if target is None or target.is_bot:
        return
    if await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>You are an admin</b>\n<blockquote>Admins can act directly, no report needed ✨</blockquote>")
        return
    if await is_admin(context.bot, chat.id, target.id):
        await ax_mod.say(update, "🛡️ <b>Can't report an admin</b>\n<blockquote>Admins are trusted here ✨</blockquote>")
        return
    admins = await context.bot.get_chat_administrators(chat.id)
    tags = "".join(f'<a href="tg://user?id={a.user.id}">\u200b</a>' for a in admins if not a.user.is_bot)
    await msg.reply_to_message.reply_text(
        f"🚨 <b>Reported</b>\n<blockquote>👤 <b>{esc(target.first_name)}</b> was reported to the admins by <b>{esc(user.first_name)}</b>. They will take a look 💜</blockquote>{tags}",
        parse_mode="HTML")


async def report_cmd(update, context):
    await _do_report(update, context)


async def atadmin_watch(update, context):
    msg = update.effective_message
    if msg and msg.reply_to_message and msg.text and _AT_ADMIN.match(msg.text):
        await _do_report(update, context)


async def reports_cmd(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private" or not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>")
        return
    pool = context.bot_data["pool"]
    arg = (context.args or [""])[0].lower()
    if arg not in ("on", "off", "yes", "no"):
        cur = await enabled(pool, chat.id)
        await ax_mod.say(update, f"⚙️ <b>Reports</b>\n<blockquote>Currently <b>{'on' if cur else 'off'}</b>\nUse /reports on or off</blockquote>")
        return
    val = arg in ("on", "yes")
    async with pool.acquire() as c:
        await c.execute("INSERT INTO ax_report_settings (chat_id, enabled) VALUES ($1,$2) "
                        "ON CONFLICT (chat_id) DO UPDATE SET enabled=$2", chat.id, val)
    await ax_mod.say(update, f"✅ <b>Reports {'on' if val else 'off'}</b>\n<blockquote>Members {'can' if val else 'can no longer'} report messages</blockquote>")


def register(app):
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("reports", reports_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.ChatType.PRIVATE, atadmin_watch), group=-1)

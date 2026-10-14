"""Forum topic tools: /newtopic /renametopic /closetopic /reopentopic /deletetopic."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import esc, is_admin

H = ParseMode.HTML


async def _say(update, text):
    return await update.effective_message.reply_text(text, parse_mode=H)


async def _guard(update, context, need_topic):
    chat, user = update.effective_chat, update.effective_user
    msg = update.effective_message
    if chat.type != "supergroup" or not getattr(chat, "is_forum", False):
        await _say(update, "<b>Topics only</b>\n<blockquote>This works in groups that have Topics turned on ✨</blockquote>")
        return False, None
    if not await is_admin(context.bot, chat.id, user.id):
        await _say(update, "🔒 <b>Admins only</b>\n<blockquote>Only admins can manage topics ✨</blockquote>")
        return False, None
    tid = msg.message_thread_id if msg.is_topic_message else None
    if need_topic and not tid:
        await _say(update, "📌 <b>Use it inside a topic</b>\n<blockquote>Send this command in the topic you want to change 💫</blockquote>")
        return False, None
    return True, tid


async def _fail(update, e):
    await _say(update, "⚠️ <b>Could not do that</b>\n<blockquote>Make sure I am admin with the Manage Topics right. %s</blockquote>" % esc(str(e)[:100]))


async def newtopic_cmd(update, context):
    ok, _ = await _guard(update, context, False)
    if not ok:
        return
    name = " ".join(context.args).strip()
    if not name:
        await _say(update, "✍️ <b>Give it a name</b>\n<blockquote>Example: /newtopic Memes and fun</blockquote>")
        return
    try:
        await context.bot.create_forum_topic(update.effective_chat.id, name[:128])
    except Exception as e:
        await _fail(update, e)
        return
    await _say(update, "✅ <b>Topic created</b>\n<blockquote>%s is ready 💫</blockquote>" % esc(name[:128]))


async def renametopic_cmd(update, context):
    ok, tid = await _guard(update, context, True)
    if not ok:
        return
    name = " ".join(context.args).strip()
    if not name:
        await _say(update, "✍️ <b>Give it a new name</b>\n<blockquote>Example: /renametopic Chill zone</blockquote>")
        return
    try:
        await context.bot.edit_forum_topic(update.effective_chat.id, tid, name=name[:128])
    except Exception as e:
        await _fail(update, e)
        return
    await _say(update, "✅ <b>Renamed</b>\n<blockquote>This topic is now %s 💫</blockquote>" % esc(name[:128]))


async def closetopic_cmd(update, context):
    ok, tid = await _guard(update, context, True)
    if not ok:
        return
    try:
        await context.bot.close_forum_topic(update.effective_chat.id, tid)
    except Exception as e:
        await _fail(update, e)
        return
    await _say(update, "🔒 <b>Topic closed</b>\n<blockquote>Only admins can write here now ✨</blockquote>")


async def reopentopic_cmd(update, context):
    ok, tid = await _guard(update, context, True)
    if not ok:
        return
    try:
        await context.bot.reopen_forum_topic(update.effective_chat.id, tid)
    except Exception as e:
        await _fail(update, e)
        return
    await _say(update, "🔓 <b>Topic reopened</b>\n<blockquote>Everyone can write here again 💫</blockquote>")


async def deletetopic_cmd(update, context):
    ok, tid = await _guard(update, context, True)
    if not ok:
        return
    try:
        await context.bot.delete_forum_topic(update.effective_chat.id, tid)
    except Exception as e:
        await _fail(update, e)


def register(app):
    app.add_handler(CommandHandler("newtopic", newtopic_cmd))
    app.add_handler(CommandHandler("renametopic", renametopic_cmd))
    app.add_handler(CommandHandler("closetopic", closetopic_cmd))
    app.add_handler(CommandHandler("reopentopic", reopentopic_cmd))
    app.add_handler(CommandHandler("deletetopic", deletetopic_cmd))

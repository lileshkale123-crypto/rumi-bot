"""Action topic: send moderation messages into one forum topic."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import is_admin

H = ParseMode.HTML
MAP = {}


async def _say(update, text):
    return await update.effective_message.reply_text(text, parse_mode=H)


async def _load(context):
    pool = context.application.bot_data.get("pool")
    if not pool:
        return
    try:
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT chat_id, thread_id FROM ax_action_topics")
        for r in rows:
            MAP[r["chat_id"]] = r["thread_id"]
        print("action topics loaded:", len(rows))
    except Exception as e:
        print("action topic load failed:", e)


async def _check(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type != "supergroup" or not getattr(chat, "is_forum", False):
        await _say(update, "<b>Topics only</b>\n<blockquote>This works in groups that have Topics turned on \u2728</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await _say(update, "\U0001F512 <b>Admins only</b>\n<blockquote>Only admins can choose the action topic \u2728</blockquote>")
        return False
    return True


async def set_cmd(update, context):
    if not await _check(update, context):
        return
    msg, chat = update.effective_message, update.effective_chat
    tid = msg.message_thread_id if msg.is_topic_message else None
    if not tid:
        await _say(update, "\U0001F4CC <b>Use it inside a topic</b>\n<blockquote>Send /setactiontopic in the topic where you want moderation messages \U0001F4AB</blockquote>")
        return
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_action_topics (chat_id, thread_id) VALUES ($1, $2) "
            "ON CONFLICT (chat_id) DO UPDATE SET thread_id = EXCLUDED.thread_id",
            chat.id, tid)
    MAP[chat.id] = tid
    await _say(update, "\u2705 <b>Action topic set</b>\n<blockquote>Bans, warns, mutes and other moderation messages will now appear in this topic \u2728</blockquote>")


async def show_cmd(update, context):
    if not await _check(update, context):
        return
    chat = update.effective_chat
    if context.args and context.args[0].lower() == "off":
        async with context.bot_data["pool"].acquire() as c:
            await c.execute("DELETE FROM ax_action_topics WHERE chat_id = $1", chat.id)
        MAP.pop(chat.id, None)
        await _say(update, "\U0001F9F9 <b>Action topic cleared</b>\n<blockquote>Moderation messages will appear where the command is used \u2728</blockquote>")
        return
    tid = MAP.get(chat.id)
    if tid:
        await _say(update, "\U0001F4CC <b>Action topic is ON</b>\n<blockquote>Moderation messages go to the topic you chose.\nTurn it off with /actiontopic off \u2728</blockquote>")
    else:
        await _say(update, "\U0001F4CC <b>Action topic is OFF</b>\n<blockquote>Open a topic and send /setactiontopic there to choose it \u2728</blockquote>")


def register(app):
    app.add_handler(CommandHandler("setactiontopic", set_cmd))
    app.add_handler(CommandHandler("actiontopic", show_cmd))
    if app.job_queue:
        app.job_queue.run_once(_load, 5)


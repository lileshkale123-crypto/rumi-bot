import logging
import re
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import ai_engine
import ai_store
import config

log = logging.getLogger(__name__)
_last_reply = {}
_trigger = re.compile(r"\b(" + "|".join(map(re.escape, config.AI_TRIGGER_WORDS)) + r")\b", re.I)
_greet = re.compile(r"^\s*(" + "|".join(config.AI_GREETINGS) + r")\b", re.I)


async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if msg is None or not msg.text or user is None or user.is_bot:
        return
    text = msg.text.strip()[:1000]
    private = chat.type == "private"
    direct = private
    if not direct:
        rt = msg.reply_to_message
        uname = (context.bot.username or "").lower()
        if rt and rt.from_user and rt.from_user.id == context.bot.id:
            direct = True
        elif _trigger.search(text) or _greet.search(text):
            direct = True
        elif uname and f"@{uname}" in text.lower():
            direct = True
    if not direct:
        return

    now = time.monotonic()
    if now - _last_reply.get(chat.id, 0) < config.AI_FLOOD_SECONDS:
        return
    _last_reply[chat.id] = now

    pool = context.bot_data["pool"]
    if not await ai_store.use_quota(pool, user.id):
        await msg.reply_text(f"we've chatted {config.AI_DAILY_LIMIT} times today 🌙 come back after midnight (IST)!")
        return

    await context.bot.send_chat_action(chat.id, ChatAction.TYPING)
    prompt = text if private else f"{user.first_name or 'Someone'}: {text}"
    reply = None
    try:
        history = await ai_store.get_history(pool, chat.id)
        reply = await ai_engine.chat(history, prompt)
    except Exception as e:
        log.warning("ai chat failed: %s", e)
    if not reply:
        await msg.reply_text("my brain is a little busy rn 🌸 try again in a moment")
        return
    await ai_store.save_turn(pool, chat.id, prompt, reply)
    await msg.reply_text(reply[:4000])

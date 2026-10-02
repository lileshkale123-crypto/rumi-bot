"""Auto-react: Rumi drops an emoji reaction on messages aimed at her."""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

import config

log = logging.getLogger(__name__)

# First match wins. Only emojis Telegram allows as reactions.
RULES = [
    (r"good ?night|\bgn\b|neend", "😴"),
    (r"lol|lmao|haha+|hehe+|😂|🤣|funny|rofl", "🤣"),
    (r"thank|thx|shukriya|dhanyawad", "🥰"),
    (r"love|luv|cute|pyaar|❤|😍", "😍"),
    (r"\bsad\b|\bcry|rona|dukhi|miss you|hurt|😢|😭", "😢"),
    (r"congrat|badhai|\bwon\b|jeet|winner|🎉", "🎉"),
    (r"awesome|amazing|\bwow\b|\bgg\b|best|great|nice|\bmast\b|zabardast", "🔥"),
    (r"good ?morning|\bgm\b|\bhi+\b|\bhey+\b|hello+|\bhlo+\b|namaste|hola", "🤗"),
    (r"\bok(ay)?\b|\bhaan\b|\bthik\b|sure", "👍"),
]
_RULES = [(re.compile(p, re.I), e) for p, e in RULES]
_trigger = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in config.AI_TRIGGER_WORDS) + r")\b", re.I
)


async def react(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if msg is None or not msg.text or user is None or user.is_bot:
        return
    text = msg.text
    aimed = chat.type == "private"
    if not aimed:
        rt = msg.reply_to_message
        uname = (context.bot.username or "").lower()
        if rt and rt.from_user and rt.from_user.id == context.bot.id:
            aimed = True
        elif _trigger.search(text) or (uname and "@" + uname in text.lower()):
            aimed = True
    if not aimed:
        return
    for rx, emoji in _RULES:
        if rx.search(text):
            try:
                await msg.set_reaction(emoji)
            except Exception as e:
                log.warning("reaction failed: %s", e)
            return


def register(application) -> None:
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, react), group=9)

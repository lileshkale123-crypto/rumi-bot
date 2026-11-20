"""Auto-react: Rumi drops an emoji reaction on messages aimed at her (aiogram 3.x)."""
import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, ReactionTypeEmoji, TelegramObject

import ax_common
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
    r"\b(" + "|".join(re.escape(w) for w in config.AI_TRIGGER_WORDS) + r")\b",
    re.I,
)


async def _react_inner(msg: Message, bot: Bot, bot_username: str) -> None:
    user = msg.from_user
    chat = msg.chat
    if not msg.text or user is None or user.is_bot:
        return

    text = msg.text
    if text.startswith("/"):
        return

    aimed = chat.type == "private"
    if not aimed:
        rt = msg.reply_to_message
        uname = (bot_username or await ax_common.get_bot_username(bot)).lower()
        if rt and rt.from_user and rt.from_user.id == bot.id:
            aimed = True
        elif _trigger.search(text) or (uname and "@" + uname in text.lower()):
            aimed = True

    if not aimed:
        return

    for rx, emoji in _RULES:
        if rx.search(text):
            try:
                await msg.react([ReactionTypeEmoji(emoji=emoji)])
            except Exception as e:
                log.warning("reaction failed: %s", e)
            return


class AutoReactMiddleware(BaseMiddleware):
    """Non-blocking middleware so auto-reactions still fire alongside ai_handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text and not event.text.startswith("/"):
            bot: Bot | None = data.get("bot")
            if bot is not None:
                ax_common.fire(_react_inner(event, bot, data.get("bot_username") or ""))
        return await handler(event, data)


def register(dp) -> None:
    dp.message.outer_middleware(AutoReactMiddleware())

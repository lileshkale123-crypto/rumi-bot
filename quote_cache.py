"""Remembers recent group messages so /q r can find the message that was replied to."""
from collections import OrderedDict

from telegram.ext import MessageHandler, filters

MAX_PER_CHAT = 300
_cache = {}


def get(chat_id, message_id):
    return _cache.get(chat_id, {}).get(message_id)


async def _remember(update, context):
    m = update.effective_message
    if m is None or not (m.text or m.caption):
        return
    d = _cache.setdefault(m.chat_id, OrderedDict())
    d[m.message_id] = m
    while len(d) > MAX_PER_CHAT:
        d.popitem(last=False)


def register(app):
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, _remember),
        group=-30,
    )

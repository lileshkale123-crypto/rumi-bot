"""Remembers recent group messages so /q r and /q N can find messages."""
from collections import OrderedDict
from telegram.ext import MessageHandler, filters

MAX_PER_CHAT = 300
_cache = {}


def get(chat_id, message_id):
    return _cache.get(chat_id, {}).get(message_id)


def span(chat_id, start_id, n, before_id):
    d = _cache.get(chat_id) or {}
    if start_id not in d:
        return []
    out, on = [], False
    for mid, m in d.items():
        on = on or mid == start_id
        if on and mid < before_id:
            out.append(m)
            if len(out) >= n:
                break
    return out


def last(chat_id, before_id, n):
    d = _cache.get(chat_id) or {}
    return [m for mid, m in d.items() if mid < before_id][-n:]


async def _remember(update, context):
    m = update.effective_message
    if m is None:
        return
    has_content = bool(
        m.text or m.caption or m.photo or m.sticker or m.video
        or m.animation or m.voice or m.audio or m.document or m.video_note
    )
    if not has_content:
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


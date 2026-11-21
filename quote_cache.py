"""Remembers recent group messages so /q r and /q N can find messages (aiogram 3.x).

Multi-bot safe: everything is keyed by (bot_id, chat_id). Strictly bounded, because every
Message object costs RAM: MAX_PER_CHAT per chat, MAX_CHATS chats and MAX_TOTAL messages
across all bots together.
"""
import weakref
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

MAX_PER_CHAT = 100
MAX_CHATS = 500
MAX_TOTAL = 3000

_cache: "OrderedDict[tuple[int, int], OrderedDict[int, Message]]" = OrderedDict()
_total = 0
_registered: "weakref.WeakSet" = weakref.WeakSet()


def get(chat_id: int, message_id: int, bot_id: int = 0) -> Message | None:
    d = _cache.get((bot_id, chat_id))
    if d is None:
        return None
    return d.get(message_id)


def span(chat_id: int, start_id: int, n: int, before_id: int, bot_id: int = 0) -> list[Message]:
    d = _cache.get((bot_id, chat_id)) or {}
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


def last(chat_id: int, before_id: int, n: int, bot_id: int = 0) -> list[Message]:
    d = _cache.get((bot_id, chat_id)) or {}
    return [m for mid, m in d.items() if mid < before_id][-n:]


def _drop_oldest_chat() -> None:
    global _total
    _key, d = _cache.popitem(last=False)
    _total -= len(d)


def remember_message(m: Message, bot_id: int = 0) -> None:
    global _total
    if m.chat.type not in ("group", "supergroup"):
        return
    if m.text and m.text.startswith("/"):
        return
    has_content = bool(
        m.text
        or m.caption
        or m.photo
        or m.sticker
        or m.video
        or m.animation
        or m.voice
        or m.audio
        or m.document
        or m.video_note
    )
    if not has_content:
        return
    key = (bot_id, m.chat.id)
    d = _cache.get(key)
    if d is None:
        d = OrderedDict()
        _cache[key] = d
    _cache.move_to_end(key)

    if m.message_id not in d:
        _total += 1
    d[m.message_id] = m
    while len(d) > MAX_PER_CHAT:
        d.popitem(last=False)
        _total -= 1
    while (len(_cache) > MAX_CHATS or _total > MAX_TOTAL) and len(_cache) > 1:
        _drop_oldest_chat()


class QuoteCacheMiddleware(BaseMiddleware):
    """Non-blocking observer that records recent group messages for /q stacking."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            bot = data.get("bot")
            remember_message(event, getattr(bot, "id", 0) or 0)
        return await handler(event, data)


def register(dp) -> None:
    """Safe to call once per dispatcher (clone bots may use their own dispatcher)."""
    if dp in _registered:
        return
    _registered.add(dp)
    dp.message.outer_middleware(QuoteCacheMiddleware())

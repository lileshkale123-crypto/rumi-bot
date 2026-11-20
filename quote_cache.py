"""Remembers recent group messages so /q r and /q N can find messages (aiogram 3.x)."""
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

MAX_PER_CHAT = 300
MAX_CHATS = 2000
_cache: OrderedDict[int, OrderedDict[int, Message]] = OrderedDict()
_middleware_registered = False


def get(chat_id: int, message_id: int) -> Message | None:
    d = _cache.get(chat_id)
    if d is None:
        return None
    return d.get(message_id)


def span(chat_id: int, start_id: int, n: int, before_id: int) -> list[Message]:
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


def last(chat_id: int, before_id: int, n: int) -> list[Message]:
    d = _cache.get(chat_id) or {}
    return [m for mid, m in d.items() if mid < before_id][-n:]


def remember_message(m: Message) -> None:
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
    cid = m.chat.id
    d = _cache.get(cid)
    if d is None:
        d = OrderedDict()
        _cache[cid] = d
    _cache.move_to_end(cid)
    while len(_cache) > MAX_CHATS:
        _cache.popitem(last=False)

    d[m.message_id] = m
    while len(d) > MAX_PER_CHAT:
        d.popitem(last=False)


class QuoteCacheMiddleware(BaseMiddleware):
    """Non-blocking observer that records recent group messages for /q stacking."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            remember_message(event)
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if _middleware_registered:
        return
    _middleware_registered = True
    dp.message.outer_middleware(QuoteCacheMiddleware())

"""DM commands and settings callbacks act on the connected group (aiogram 3.x middleware)."""
import logging
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject
from pydantic import PrivateAttr

from ax_common import is_admin

log = logging.getLogger(__name__)
H = ParseMode.HTML

CMDS = {
    "save", "get", "notes", "clear", "clearallnotes",
    "filter", "filters", "stop", "clearfilters",
    "lock", "unlock", "locks", "rules", "setrules", "resetrules",
    "warnings", "warnlimit", "warnmode", "warntime",
    "welcome", "setwelcome", "resetwelcome",
    "goodbye", "setgoodbye", "resetgoodbye",
    "setflood", "flood", "settings",
}

_conn_cache: dict[int, tuple[float, int | None]] = {}
_CONN_TTL = 60.0
_CONN_MAX = 5000
_middleware_registered = False


def invalidate(user_id: int) -> None:
    _conn_cache.pop(user_id, None)


def _prune_conn_cache(now: float) -> None:
    if len(_conn_cache) <= _CONN_MAX:
        return
    expired = [uid for uid, (ts, _) in _conn_cache.items() if now - ts >= _CONN_TTL]
    for uid in expired:
        _conn_cache.pop(uid, None)
    if len(_conn_cache) > _CONN_MAX:
        oldest = sorted(_conn_cache.items(), key=lambda kv: kv[1][0])[: _CONN_MAX // 5]
        for uid, _ in oldest:
            _conn_cache.pop(uid, None)


async def _connected(pool: asyncpg.Pool, user_id: int) -> int | None:
    now = time.time()
    hit = _conn_cache.get(user_id)
    if hit and now - hit[0] < _CONN_TTL:
        return hit[1]
    cid = None
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow("SELECT chat_id FROM ax_connections WHERE user_id=$1", user_id)
        if r and r["chat_id"]:
            cid = int(r["chat_id"])
    except Exception:
        pass
    _prune_conn_cache(now)
    _conn_cache[user_id] = (now, cid)
    return cid


def _extract_cmd(message: Message) -> str | None:
    raw = (message.text or message.caption or "").strip()
    if not raw.startswith("/"):
        return None
    first = raw.split(None, 1)[0][1:]
    return first.split("@", 1)[0].lower()


class _DMProxyMessage(Message):
    """Message subclass whose .chat points to the connected group while all replies/edits target the DM."""

    _orig_msg: Message = PrivateAttr()

    def answer(self, *args, **kwargs):
        return self._orig_msg.answer(*args, **kwargs)

    def reply(self, *args, **kwargs):
        return self._orig_msg.reply(*args, **kwargs)

    def answer_photo(self, *args, **kwargs):
        return self._orig_msg.answer_photo(*args, **kwargs)

    def reply_photo(self, *args, **kwargs):
        return self._orig_msg.reply_photo(*args, **kwargs)

    def answer_video(self, *args, **kwargs):
        return self._orig_msg.answer_video(*args, **kwargs)

    def reply_video(self, *args, **kwargs):
        return self._orig_msg.reply_video(*args, **kwargs)

    def answer_animation(self, *args, **kwargs):
        return self._orig_msg.answer_animation(*args, **kwargs)

    def reply_animation(self, *args, **kwargs):
        return self._orig_msg.reply_animation(*args, **kwargs)

    def answer_sticker(self, *args, **kwargs):
        return self._orig_msg.answer_sticker(*args, **kwargs)

    def reply_sticker(self, *args, **kwargs):
        return self._orig_msg.reply_sticker(*args, **kwargs)

    def answer_voice(self, *args, **kwargs):
        return self._orig_msg.answer_voice(*args, **kwargs)

    def reply_voice(self, *args, **kwargs):
        return self._orig_msg.reply_voice(*args, **kwargs)

    def answer_audio(self, *args, **kwargs):
        return self._orig_msg.answer_audio(*args, **kwargs)

    def reply_audio(self, *args, **kwargs):
        return self._orig_msg.reply_audio(*args, **kwargs)

    def answer_document(self, *args, **kwargs):
        return self._orig_msg.answer_document(*args, **kwargs)

    def reply_document(self, *args, **kwargs):
        return self._orig_msg.reply_document(*args, **kwargs)

    def edit_text(self, *args, **kwargs):
        return self._orig_msg.edit_text(*args, **kwargs)

    def edit_caption(self, *args, **kwargs):
        return self._orig_msg.edit_caption(*args, **kwargs)

    def edit_reply_markup(self, *args, **kwargs):
        return self._orig_msg.edit_reply_markup(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._orig_msg.delete(*args, **kwargs)


def _proxy_message(msg: Message, cid: int, title: str | None = None) -> _DMProxyMessage:
    fake_chat = Chat(id=cid, type="supergroup", title=title, is_forum=False, username=None)
    field_data = {name: getattr(msg, name) for name in Message.model_fields}
    field_data["chat"] = fake_chat
    proxy = _DMProxyMessage.model_construct(_fields_set=set(msg.model_fields_set), **field_data)
    proxy._orig_msg = msg
    if msg.bot is not None:
        proxy.as_(msg.bot)
    return proxy


class DMProxyMessageMiddleware(BaseMiddleware):
    """Proxies DM commands in CMDS so they operate on the user's connected group."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type == "private"
            and event.from_user is not None
        ):
            cmd = _extract_cmd(event)
            if cmd in CMDS:
                bot: Bot | None = data.get("bot")
                pool: asyncpg.Pool | None = data.get("pool")
                if bot is not None and pool is not None:
                    cid = await _connected(pool, event.from_user.id)
                    if not cid:
                        await event.reply(
                            "\U0001F517 <b>Connect a group first</b>\n"
                            "<blockquote>Send /connect inside your group and tap the button. "
                            "After that, these commands work here and change that group \u2728</blockquote>",
                            parse_mode=H,
                        )
                        return None
                    if not await is_admin(bot, cid, event.from_user.id):
                        await event.reply(
                            "\U0001F512 <b>Admins only</b>\n"
                            "<blockquote>You are no longer an admin of the connected group. "
                            "Use /disconnect or /connect again \u2728</blockquote>",
                            parse_mode=H,
                        )
                        return None
                    title = None
                    try:
                        title = await pool.fetchval("SELECT title FROM ax_groups WHERE chat_id=$1", cid)
                    except Exception:
                        pass
                    proxy = _proxy_message(event, cid, title=title)
                    return await handler(proxy, data)
        return await handler(event, data)


class DMProxyCallbackMiddleware(BaseMiddleware):
    """Proxies DM ^axs: settings callbacks so they operate on the user's connected group."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, CallbackQuery)
            and event.data
            and event.data.startswith("axs:")
            and isinstance(event.message, Message)
            and event.message.chat.type == "private"
        ):
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                cid = await _connected(pool, event.from_user.id)
                if not cid:
                    await event.answer("Send /connect in your group first", show_alert=True)
                    return None
                if not await is_admin(bot, cid, event.from_user.id):
                    await event.answer("You are no longer an admin there", show_alert=True)
                    return None
                proxy_msg = _proxy_message(event.message, cid)
                proxy_q = event.model_copy(update={"message": proxy_msg})
                if event.bot is not None:
                    proxy_q.as_(event.bot)
                return await handler(proxy_q, data)
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if _middleware_registered:
        return
    _middleware_registered = True
    dp.message.outer_middleware(DMProxyMessageMiddleware())
    dp.callback_query.outer_middleware(DMProxyCallbackMiddleware())
    log.info("DM proxy middleware registered for %d commands + ^axs: callbacks", len(CMDS))

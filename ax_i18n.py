"""Hindi swap: English bot text -> lang_hi.json for members who chose Hindi (aiogram 3.x middleware)."""
import contextvars
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import (
    EditMessageCaption,
    EditMessageText,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.methods.base import Response, TelegramType
from aiogram.types import TelegramObject, Update

log = logging.getLogger(__name__)

HI_USERS: set[int] = set()
CUR: contextvars.ContextVar[int | None] = contextvars.ContextVar("axi_user", default=None)
EXACT: dict[str, str] = {}
PATS: list[tuple[re.Pattern, str, list[str]]] = []
PH = re.compile(r"%s|%d|\{[a-z_0-9]*\}")

_loaded_file = False
_registered = False


def _load_file() -> None:
    global _loaded_file
    if _loaded_file:
        return
    _loaded_file = True
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang_hi.json")
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return
    for k, v in d.items():
        if not v:
            continue
        EXACT[k] = v
        pct = any(t in ("%s", "%d") for t in PH.findall(k))
        kk = k.replace("%%", "%") if pct else k
        hv = v.replace("%%", "%") if pct else v
        toks = PH.findall(kk)
        if not toks or len(PH.sub("", kk).strip()) < 12:
            continue
        rx, pos = "", 0
        for m in PH.finditer(kk):
            rx += re.escape(kk[pos : m.start()]) + "(.*?)"
            pos = m.end()
        rx += re.escape(kk[pos:])
        try:
            PATS.append((re.compile(rx, re.S), hv, toks))
        except re.error:
            pass


def _fill(hv: str, toks: list[str], groups: tuple[str, ...]) -> str:
    pos = [g for t, g in zip(toks, groups) if t in ("%s", "%d")]
    named: dict[str, str] = {}
    for t, g in zip(toks, groups):
        if t.startswith("{"):
            named.setdefault(t, g)
    it = iter(pos)

    def sub(m: re.Match) -> str:
        t = m.group(0)
        return next(it) if t in ("%s", "%d") else named[t]

    return PH.sub(sub, hv)


def tr(text: str | None) -> str | None:
    if not isinstance(text, str) or not text:
        return text
    v = EXACT.get(text)
    if v:
        return v
    for rx, hv, toks in PATS:
        m = rx.fullmatch(text)
        if m:
            try:
                return _fill(hv, toks, m.groups())
            except Exception:
                return text
    return text


def _hi(chat_id: int | str | None) -> bool:
    u = chat_id if isinstance(chat_id, int) and chat_id > 0 else CUR.get()
    return u in HI_USERS


def _update_user_id(update: Update) -> int | None:
    for attr in (
        "message",
        "edited_message",
        "callback_query",
        "inline_query",
        "chosen_inline_result",
        "pre_checkout_query",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
    ):
        obj = getattr(update, attr, None)
        if obj is not None:
            u = getattr(obj, "from_user", None)
            if u is not None:
                return u.id
    return None


class I18nUserMiddleware(BaseMiddleware):
    """Records the active user id in ContextVar so outgoing responses can be localized."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            uid = _update_user_id(event)
            if uid is not None:
                token = CUR.set(uid)
                try:
                    return await handler(event, data)
                finally:
                    CUR.reset(token)
        return await handler(event, data)


class I18nRequestMiddleware(BaseRequestMiddleware):
    """Translates outgoing SendMessage/EditMessageText/SendPhoto/EditMessageCaption for Hindi users."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if HI_USERS:
            try:
                if isinstance(method, (SendMessage, EditMessageText)) and _hi(method.chat_id):
                    if method.text:
                        translated = tr(method.text)
                        if translated != method.text:
                            method = method.model_copy(update={"text": translated})
                elif isinstance(method, (SendPhoto, EditMessageCaption)) and _hi(method.chat_id):
                    if method.caption:
                        translated = tr(method.caption)
                        if translated != method.caption:
                            method = method.model_copy(update={"caption": translated})
            except Exception:
                pass
        return await make_request(bot, method)


async def load(pool: asyncpg.Pool) -> None:
    try:
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT user_id FROM ax_user_lang WHERE lang='hi'")
        HI_USERS.clear()
        HI_USERS.update(r["user_id"] for r in rows)
    except Exception:
        pass


def set_lang(uid: int, code: str) -> None:
    if code == "hi":
        HI_USERS.add(uid)
    else:
        HI_USERS.discard(uid)


def register(dp, bot: Bot) -> None:
    global _registered
    _load_file()
    if _registered:
        return
    _registered = True
    dp.update.outer_middleware(I18nUserMiddleware())
    bot.session.middleware(I18nRequestMiddleware())

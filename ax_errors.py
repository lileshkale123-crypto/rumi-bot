"""Global aiogram 3.x error handler with quiet filtering and per-chat notice throttling."""
import logging
import time

from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import ErrorEvent, Message, Update

log = logging.getLogger(__name__)
_last_notice: dict[int, float] = {}
_LAST_MAX = 5000
_registered = False

_QUIET = (
    "message is not modified",
    "message to be replied not found",
    "message to edit not found",
    "query is too old",
)


def _extract_message(update: Update | None) -> Message | None:
    if update is None:
        return None
    if update.message is not None:
        return update.message
    if update.callback_query is not None and isinstance(update.callback_query.message, Message):
        return update.callback_query.message
    if update.edited_message is not None:
        return update.edited_message
    return None


def _wants_notice(update: Update | None) -> bool:
    if update is None:
        return False
    if update.callback_query is not None:
        return True
    m = _extract_message(update)
    t = (m.text or m.caption or "") if m else ""
    return t.startswith("/")


async def on_error(event: ErrorEvent) -> bool:
    err = event.exception
    update = event.update

    if isinstance(err, (TelegramConflictError, TelegramNetworkError, TelegramRetryAfter)):
        log.warning("telegram transient error: %s", err)
        return True

    if isinstance(err, TelegramBadRequest) and any(p in str(err).lower() for p in _QUIET):
        log.warning("harmless bad request: %s", err)
        return True

    log.error("Unhandled error on update %s", getattr(update, "update_id", None), exc_info=err)

    msg = _extract_message(update)
    if msg is not None and _wants_notice(update):
        cid = msg.chat.id
        now = time.time()
        if len(_last_notice) > _LAST_MAX:
            stale = [k for k, ts in _last_notice.items() if now - ts >= 60.0]
            for k in stale:
                _last_notice.pop(k, None)
        if now - _last_notice.get(cid, 0.0) < 30.0:
            return True
        _last_notice[cid] = now
        try:
            await msg.reply(
                "⚠️ <b>Something went wrong</b>\n"
                "<blockquote>I noted it. Please try again in a moment 💫</blockquote>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            log.warning("could not send error notice")
    return True


def register(dp) -> None:
    global _registered
    if _registered:
        return
    _registered = True
    dp.errors.register(on_error)

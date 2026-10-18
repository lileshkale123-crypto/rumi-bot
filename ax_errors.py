import logging
import time
from telegram import Update
from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter
from telegram.ext import Application, ContextTypes

log = logging.getLogger(__name__)
_last_notice = {}
_QUIET = (
    "message is not modified",
    "message to be replied not found",
    "message to edit not found",
    "query is too old",
)


def _wants_notice(update) -> bool:
    if update.callback_query:
        return True
    m = update.effective_message
    t = (m.text or m.caption or '') if m else ''
    return t.startswith('/')


async def on_error(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (Conflict, NetworkError, RetryAfter)):
        log.warning("telegram transient error: %s", err)
        return
    if isinstance(err, BadRequest) and any(p in str(err).lower() for p in _QUIET):
        log.warning("harmless bad request: %s", err)
        return
    log.error("Unhandled error", exc_info=err)
    if isinstance(update, Update) and update.effective_chat and update.effective_message and _wants_notice(update):
        cid = update.effective_chat.id
        now = time.time()
        if now - _last_notice.get(cid, 0) < 30:
            return
        _last_notice[cid] = now
        try:
            await update.effective_message.reply_text(
                "⚠️ <b>Something went wrong</b>\n"
                "<blockquote>I noted it. Please try again in a moment 💫</blockquote>",
                parse_mode="HTML",
            )
        except Exception:
            log.warning("could not send error notice")


def register(application: Application) -> None:
    application.add_error_handler(on_error)

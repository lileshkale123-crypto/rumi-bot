"""Auto-delete the bot's replies to chosen commands in groups."""
import contextvars

from telegram import Message
from telegram.ext import CommandHandler

DELETE_AFTER_SECONDS = 15 * 60
DELETE_COMMANDS = {
    "wallet", "claim", "pay", "guard", "rob", "rankings",
    "vault", "deposit", "withdraw", "work", "spin", "mafiatop",
}

_active_jq = contextvars.ContextVar("autodel_jq", default=None)


async def _delete_job(context):
    chat_id, message_id = context.job.data
    try:
        await context.bot.delete_message(chat_id, message_id)
    except Exception:
        pass  # already deleted or too old


def _patch_reply(name):
    orig = getattr(Message, name)

    async def inner(self, *args, **kwargs):
        sent = await orig(self, *args, **kwargs)
        jq = _active_jq.get()
        if jq is not None and self.chat.type in ("group", "supergroup"):
            try:
                jq.run_once(_delete_job, DELETE_AFTER_SECONDS,
                            data=(sent.chat_id, sent.message_id))
            except Exception:
                pass
        return sent

    setattr(Message, name, inner)


def _wrap(callback, jq):
    async def wrapped(update, context):
        token = _active_jq.set(jq)
        try:
            return await callback(update, context)
        finally:
            _active_jq.reset(token)

    return wrapped


def install(application):
    for name in ("reply_text", "reply_html", "reply_photo"):
        _patch_reply(name)
    jq = application.job_queue
    count = 0
    for handlers in application.handlers.values():
        for h in handlers:
            if isinstance(h, CommandHandler) and (h.commands & DELETE_COMMANDS):
                h.callback = _wrap(h.callback, jq)
                count += 1
    print(f"Auto-delete active on {count} commands")

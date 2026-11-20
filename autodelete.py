"""Auto-delete the bot's replies to chosen commands in groups (aiogram 3.x + APScheduler)."""
import contextvars
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import SendMessage, SendPhoto, TelegramMethod
from aiogram.methods.base import Response, TelegramType
from aiogram.types import Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger(__name__)

DELETE_AFTER_SECONDS = 15 * 60
DELETE_COMMANDS = {
    "wallet", "claim", "pay", "guard", "rob", "rankings",
    "vault", "deposit", "withdraw", "work", "spin", "mafiatop",
}

# Holds (target_chat_id, scheduler) for the active auto-delete command execution
_active_ctx: contextvars.ContextVar[tuple[int, AsyncIOScheduler] | None] = contextvars.ContextVar(
    "autodel_ctx",
    default=None,
)


async def _delete_job(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass  # already deleted or too old


def _extract_command(text: str | None, bot_username: str) -> str | None:
    if not text or not text.startswith("/"):
        return None
    first = text.strip().split(None, 1)[0][1:]
    if "@" in first:
        cmd, target = first.split("@", 1)
        if bot_username and target.lower() != bot_username.lower():
            return None
        return cmd.lower()
    return first.lower()


class AutoDeleteMessageMiddleware(BaseMiddleware):
    """Marks group invocations of DELETE_COMMANDS so outgoing replies are scheduled for deletion."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type in ("group", "supergroup")
            and event.text
        ):
            cmd = _extract_command(event.text, data.get("bot_username") or "")
            scheduler: AsyncIOScheduler | None = data.get("scheduler")
            if cmd in DELETE_COMMANDS and scheduler is not None:
                token = _active_ctx.set((event.chat.id, scheduler))
                try:
                    return await handler(event, data)
                finally:
                    _active_ctx.reset(token)
        return await handler(event, data)


class AutoDeleteRequestMiddleware(BaseRequestMiddleware):
    """Schedules deletion of SendMessage / SendPhoto replies sent inside DELETE_COMMANDS."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        response = await make_request(bot, method)
        ctx = _active_ctx.get()
        if ctx is None or not isinstance(method, (SendMessage, SendPhoto)):
            return response

        target_chat_id, scheduler = ctx
        sent = response.result if hasattr(response, "result") else response
        if isinstance(sent, Message) and sent.chat.id == target_chat_id:
            try:
                run_at = datetime.now(timezone.utc) + timedelta(seconds=DELETE_AFTER_SECONDS)
                scheduler.add_job(
                    _delete_job,
                    trigger="date",
                    run_date=run_at,
                    args=[bot, sent.chat.id, sent.message_id],
                    id=f"autodel:{sent.chat.id}:{sent.message_id}",
                    replace_existing=True,
                )
            except Exception as exc:
                log.debug("Failed scheduling auto-delete: %s", exc)
        return response


def register(dp, bot: Bot) -> None:
    dp.message.outer_middleware(AutoDeleteMessageMiddleware())
    bot.session.middleware(AutoDeleteRequestMiddleware())
    log.info("Auto-delete active on %d commands", len(DELETE_COMMANDS))


# Backwards-compatible alias
install = register

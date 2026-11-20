"""Speed tools: (1) logs where the time goes for every group command, (2) keeps the
connection to Telegram warm so the first call after a quiet moment is not slow (aiogram 3.x + APScheduler).
Turn the log off with the environment variable RUMI_PERF=0."""
import contextvars
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import Response, TelegramType
from aiogram.types import Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

LOG = os.environ.get("RUMI_PERF", "1") != "0"
_ctx: contextvars.ContextVar[tuple[float, str] | None] = contextvars.ContextVar(
    "rumi_perf",
    default=None,
)
_registered = False


class PerfMessageMiddleware(BaseMiddleware):
    """Marks the start time of every group /command for API latency profiling."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type != "private"
            and event.text
            and event.text.startswith("/")
        ):
            cmd = event.text.split()[0].split("@")[0][:24]
            token = _ctx.set((time.perf_counter(), cmd))
            try:
                return await handler(event, data)
            finally:
                _ctx.reset(token)
        return await handler(event, data)


class PerfRequestMiddleware(BaseRequestMiddleware):
    """Logs outgoing Bot API call timing during a profiled group /command."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)
        c = _ctx.get()
        if c is None:
            return await make_request(bot, method)
        start = time.perf_counter()
        try:
            return await make_request(bot, method)
        finally:
            end = time.perf_counter()
            print(
                "[perf] %s  +%4.0fms before %s  (call took %.0fms)"
                % (
                    c[1],
                    (start - c[0]) * 1000,
                    getattr(method, "__api_method__", type(method).__name__),
                    (end - start) * 1000,
                ),
                flush=True,
            )


async def _ping(bot: Bot) -> None:
    try:
        await bot.get_me()
    except Exception:
        pass


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    scheduler.add_job(
        _ping,
        trigger="interval",
        seconds=4,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=5),
        args=[bot],
        id="ax_perf:keepwarm",
        replace_existing=True,
    )


def register(dp, bot: Bot) -> None:
    global _registered
    if _registered:
        return
    _registered = True
    if LOG:
        dp.message.outer_middleware(PerfMessageMiddleware())
        bot.session.middleware(PerfRequestMiddleware())

"""Per-bot context, middleware, and central branding for Rumi + future clones."""
import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject

import config

log = logging.getLogger(__name__)


@dataclass
class BotContext:
    bot_id: int
    username: str = ""
    display_name: str = "Rumi"
    bot_type: str = "rumi"
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Brand:
    name: str = "Rumi"
    upper_name: str = "RUMI"
    emoji: str = "💜"
    currency_symbol: str = "$"


_MAIN_CTX: BotContext | None = None
_BRANDS: dict[int, Brand] = {}
CURRENT_BOT_CTX: contextvars.ContextVar[BotContext | None] = contextvars.ContextVar(
    "rumi_bot_ctx",
    default=None,
)


def parse_bot_id_from_token(token: str) -> int:
    """Read numeric Telegram bot_id from a BotFather token without ever logging the token."""
    raw = (token or "").strip()
    head = raw.split(":", 1)[0]
    if not head.isdigit():
        raise ValueError("Invalid BOT_TOKEN format (missing numeric bot id prefix)")
    return int(head)


def register_brand(ctx: BotContext) -> Brand:
    name = (ctx.display_name or "Rumi").strip() or "Rumi"
    emoji = str(ctx.settings.get("brand_emoji") or "💜")
    currency = str(ctx.settings.get("currency_symbol") or getattr(config, "CURRENCY_SYMBOL", "$"))
    brand = Brand(
        name=name,
        upper_name=name.upper(),
        emoji=emoji,
        currency_symbol=currency,
    )
    _BRANDS[ctx.bot_id] = brand
    return brand


def set_main_context(ctx: BotContext) -> None:
    global _MAIN_CTX
    _MAIN_CTX = ctx
    register_brand(ctx)


def get_main_context() -> BotContext:
    if _MAIN_CTX is not None:
        return _MAIN_CTX
    return BotContext(bot_id=0, username="", display_name="Rumi", bot_type="rumi")


def resolve_bot_id(bot_id: int | None = None) -> int:
    """Return explicit bot_id, else active update's bot_id, else main bot_id."""
    if bot_id is not None:
        return int(bot_id)
    cur = CURRENT_BOT_CTX.get()
    if cur is not None:
        return cur.bot_id
    return get_main_context().bot_id


def get_brand(bot_id: int | None = None) -> Brand:
    """Central place for bot display name and branding."""
    bid = resolve_bot_id(bot_id)
    hit = _BRANDS.get(bid)
    if hit is not None:
        return hit
    return Brand(
        name="Rumi",
        upper_name="RUMI",
        emoji="💜",
        currency_symbol=getattr(config, "CURRENCY_SYMBOL", "$"),
    )


class BotContextMiddleware(BaseMiddleware):
    """Loads BotContext for every update and passes `bot_ctx` + `bot_id` to handlers."""

    def __init__(
        self,
        loader: Callable[[int], Awaitable[BotContext | None]] | None = None,
    ) -> None:
        self._loader = loader

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ctx: BotContext | None = None
        if self._loader is not None:
            bot: Bot | None = data.get("bot")
            if bot is not None:
                try:
                    ctx = await self._loader(bot.id)
                except Exception:
                    log.exception("Failed loading BotContext for bot_id=%s", getattr(bot, "id", "?"))
        if ctx is None:
            ctx = get_main_context()

        data["bot_ctx"] = ctx
        data["bot_id"] = ctx.bot_id
        token = CURRENT_BOT_CTX.set(ctx)
        try:
            return await handler(event, data)
        finally:
            CURRENT_BOT_CTX.reset(token)

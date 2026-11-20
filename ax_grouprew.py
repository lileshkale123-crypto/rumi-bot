"""20k cash for adding Rumi to a group with 50+ members (once per group) (aiogram 3.x)."""
import html
import logging
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.types import ChatMemberUpdated, TelegramObject

import services
from ax_common import fire
from ax_referral import _credit

log = logging.getLogger(__name__)
H = ParseMode.HTML
REWARD = 20000
MIN_MEMBERS = 50
_READY = [False]
_middleware_registered = False


async def _ensure(pool: asyncpg.Pool) -> None:
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_group_rewards ("
            "chat_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    _READY[0] = True


async def on_added(event: ChatMemberUpdated, bot: Bot, pool: asyncpg.Pool) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    if old not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return
    if new not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return
    adder = event.from_user
    if adder is None or adder.is_bot:
        return

    try:
        count = await bot.get_chat_member_count(event.chat.id)
    except Exception:
        return
    if count < MIN_MEMBERS:
        return

    try:
        await _ensure(pool)
        await services.ensure_user_and_wallet(pool, adder.id, adder.username, adder.first_name)
        async with pool.acquire() as c:
            async with c.transaction():
                ins = await c.fetchval(
                    "INSERT INTO ax_group_rewards (chat_id, user_id) VALUES ($1,$2) "
                    "ON CONFLICT DO NOTHING RETURNING chat_id",
                    event.chat.id,
                    adder.id,
                )
                if ins is None:
                    return
                if not await _credit(c, adder.id, REWARD, "group_add_reward"):
                    raise RuntimeError("adder has no wallet")
    except Exception:
        log.exception("group add reward failed")
        return

    try:
        await bot.send_message(
            event.chat.id,
            "🎁 <b>Thank you, %s</b>\n<blockquote>You added Rumi to a group with "
            "%d members and earned <code>20,000</code> cash 💖</blockquote>"
            % (html.escape(adder.first_name or "Friend"), count),
            parse_mode=H,
        )
    except Exception:
        pass


class GroupRewardMiddleware(BaseMiddleware):
    """Non-blocking observer on my_chat_member so ax_addprompt / ax_track also run."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, ChatMemberUpdated):
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                fire(on_added(event, bot, pool))
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if _middleware_registered:
        return
    _middleware_registered = True
    dp.my_chat_member.outer_middleware(GroupRewardMiddleware())

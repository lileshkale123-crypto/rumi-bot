"""Owner-only: ban a user from all fun/economy commands per bot (aiogram 3.x)."""
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

import bot_context
from ax_adminpanel import OWNER_IDS
from ax_common import cmd_args
from ax_funmode import FUN_CMDS
from bot_context import BotContext

router = Router(name="ax_ban")
H = ParseMode.HTML
_FUN_SET = set(FUN_CMDS)
# Stores (bot_id, user_id); middleware also tolerates bare user_id added by legacy callers
_banned: set[Any] = set()
_middleware_registered = False


def ban_cache_add(user_id: int, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    _banned.add((bid, user_id))


def ban_cache_remove(user_id: int, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    _banned.discard((bid, user_id))
    _banned.discard(user_id)


def is_banned(user_id: int, bot_id: int | None = None) -> bool:
    bid = bot_context.resolve_bot_id(bot_id)
    return (bid, user_id) in _banned or user_id in _banned


async def _load(pool: asyncpg.Pool) -> None:
    main_bid = bot_context.get_main_context().bot_id
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT bot_id, user_id FROM ax_bot_bans")
    _banned.clear()
    for r in rows:
        bid = r["bot_id"] or main_bid
        _banned.add((bid, r["user_id"]))


@router.message(Command("banuser"))
async def banuser_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = cmd_args(message)
    if not args or not args[0].isdigit():
        await message.reply("Usage: /banuser user_id")
        return
    target_id = int(args[0])
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_bot_bans (bot_id, user_id) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET bot_id = EXCLUDED.bot_id",
            bid,
            target_id,
        )
    ban_cache_add(target_id, bid)
    await message.reply(
        f"\U0001f6ab Banned <code>{target_id}</code> from economy/games bot-wide.",
        parse_mode=H,
    )


@router.message(Command("unbanuser"))
async def unbanuser_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = cmd_args(message)
    if not args or not args[0].isdigit():
        await message.reply("Usage: /unbanuser user_id")
        return
    target_id = int(args[0])
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_bot_bans WHERE bot_id = $1 AND user_id = $2",
            bid,
            target_id,
        )
    ban_cache_remove(target_id, bid)
    await message.reply(f"\u2705 Unbanned <code>{target_id}</code>.", parse_mode=H)


class BotBanMiddleware(BaseMiddleware):
    """Blocks bot-wide banned users from running fun/economy commands for the active bot."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            bot = data.get("bot")
            bid = data.get("bot_id") or bot_context.resolve_bot_id(getattr(bot, "id", None))
            if is_banned(event.from_user.id, bid):
                raw = (event.text or event.caption or "").strip()
                if raw.startswith("/"):
                    cmd = raw[1:].split()[0].split("@")[0].lower()
                    if cmd in _FUN_SET:
                        return None
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(BotBanMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

"""Owner-only: ban a user from all fun/economy commands bot-wide (aiogram 3.x)."""
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

from ax_adminpanel import OWNER_IDS
from ax_common import cmd_args
from ax_funmode import FUN_CMDS

router = Router(name="ax_ban")
H = ParseMode.HTML
_FUN_SET = set(FUN_CMDS)
_banned: set[int] = set()
_middleware_registered = False


async def _load(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT user_id FROM ax_bot_bans")
    _banned.clear()
    _banned.update(r["user_id"] for r in rows)


@router.message(Command("banuser"))
async def banuser_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = cmd_args(message)
    if not args or not args[0].isdigit():
        await message.reply("Usage: /banuser user_id")
        return
    target_id = int(args[0])
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_bot_bans (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
            target_id,
        )
    _banned.add(target_id)
    await message.reply(
        f"\U0001f6ab Banned <code>{target_id}</code> from economy/games bot-wide.",
        parse_mode=H,
    )


@router.message(Command("unbanuser"))
async def unbanuser_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = cmd_args(message)
    if not args or not args[0].isdigit():
        await message.reply("Usage: /unbanuser user_id")
        return
    target_id = int(args[0])
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_bot_bans WHERE user_id=$1", target_id)
    _banned.discard(target_id)
    await message.reply(f"\u2705 Unbanned <code>{target_id}</code>.", parse_mode=H)


class BotBanMiddleware(BaseMiddleware):
    """Blocks bot-wide banned users from running fun/economy commands."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.from_user is not None
            and event.from_user.id in _banned
        ):
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

"""Disable and enable commands per chat (aiogram 3.x)."""
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

from ax_common import cmd_args, esc, is_admin

router = Router(name="ax_disable")
H = ParseMode.HTML
PROTECTED = {
    "disable", "enable", "disabled", "disableable",
    "disabledel", "disableadmin", "start", "help",
}
_cache: dict[int, tuple[set[str], bool, bool]] = {}
_CACHE_MAX = 5000
_middleware_registered = False


async def load(pool: asyncpg.Pool, chat_id: int) -> tuple[set[str], bool, bool]:
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT cmd FROM ax_disabled WHERE chat_id=$1", chat_id)
        s = await c.fetchrow(
            "SELECT del_msg, admin_too FROM ax_disable_settings WHERE chat_id=$1",
            chat_id,
        )
    d = (
        {r["cmd"] for r in rows},
        bool(s and s["del_msg"]),
        bool(s and s["admin_too"]),
    )
    if len(_cache) >= _CACHE_MAX and chat_id not in _cache:
        _cache.pop(next(iter(_cache)), None)
    _cache[chat_id] = d
    return d


async def _gate(message: Message, bot: Bot, pool: asyncpg.Pool):
    chat = message.chat
    user = message.from_user
    if user is None:
        return None
    if chat.type == "private" or not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await message.reply(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>",
            parse_mode=H,
        )
        return None
    return pool, chat.id


def _name(args: list[str]) -> str:
    return args[0].lower().lstrip("/").split("@")[0] if args else ""


@router.message(Command("disable"))
async def disable(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    n = _name(cmd_args(message))
    if not n:
        await message.reply("<blockquote>Use /disable command</blockquote>", parse_mode=H)
        return
    if n in PROTECTED:
        await message.reply(
            "🔒 <b>Protected</b>\n<blockquote>That command cannot be disabled ✨</blockquote>",
            parse_mode=H,
        )
        return
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_disabled VALUES ($1,$2) ON CONFLICT DO NOTHING",
            cid,
            n,
        )
    await load(pool, cid)
    await message.reply(f"🚫 <b>/{esc(n)} disabled</b>", parse_mode=H)


@router.message(Command("enable"))
async def enable(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    g = await _gate(message, bot, pool)
    if not g:
        return
    pool, cid = g
    n = _name(cmd_args(message))
    if not n:
        await message.reply("<blockquote>Use /enable command</blockquote>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_disabled WHERE chat_id=$1 AND cmd=$2", cid, n)
    await load(pool, cid)
    await message.reply(f"✅ <b>/{esc(n)} enabled</b>", parse_mode=H)


@router.message(Command("disabled"))
async def disabled(message: Message, pool: asyncpg.Pool) -> None:
    d, _, _ = await load(pool, message.chat.id)
    body = "\n".join("/" + esc(x) for x in sorted(d)) or "nothing"
    await message.reply(
        f"🚫 <b>Disabled commands</b>\n<blockquote>{body}</blockquote>",
        parse_mode=H,
    )


@router.message(Command("disableable"))
async def disableable(message: Message) -> None:
    await message.reply(
        "🔒 <b>Cannot be disabled</b>\n<blockquote>"
        + ", ".join("/" + p for p in sorted(PROTECTED))
        + "\nEvery other command can ✨</blockquote>",
        parse_mode=H,
    )


def _flag(col: str, name: str):
    assert col in ("del_msg", "admin_too")

    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        g = await _gate(message, bot, pool)
        if not g:
            return
        pool, cid = g
        args = cmd_args(message)
        a = args[0].lower() if args else ""
        if a not in ("on", "off", "yes", "no"):
            await message.reply(f"<blockquote>Use /{name} on or off</blockquote>", parse_mode=H)
            return
        v = a in ("on", "yes")
        async with pool.acquire() as c:
            await c.execute(
                f"INSERT INTO ax_disable_settings (chat_id, {col}) "
                f"VALUES ($1,$2) ON CONFLICT (chat_id) DO UPDATE SET {col}=$2",
                cid,
                v,
            )
        await load(pool, cid)
        await message.reply(
            f"{'✅' if v else '🚫'} <b>{name} {'on' if v else 'off'}</b>",
            parse_mode=H,
        )

    return cmd


class DisableMiddleware(BaseMiddleware):
    """Blocks disabled commands in groups before command routers run."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type in ("group", "supergroup")
            and event.from_user is not None
        ):
            raw = (event.text or event.caption or "").strip()
            if raw.startswith("/"):
                first = raw.split()[0][1:].lower().split("@")[0]
                if first and first not in PROTECTED:
                    bot: Bot | None = data.get("bot")
                    pool: asyncpg.Pool | None = data.get("pool")
                    if bot is not None and pool is not None:
                        d = _cache.get(event.chat.id)
                        if d is None:
                            d = await load(pool, event.chat.id)
                        cmds, del_msg, admin_too = d
                        if first in cmds:
                            if admin_too or not await is_admin(bot, event.chat.id, event.from_user.id):
                                if del_msg:
                                    try:
                                        await event.delete()
                                    except Exception:
                                        pass
                                return None
        return await handler(event, data)


router.message(Command("disabledel"))(_flag("del_msg", "disabledel"))
router.message(Command("disableadmin"))(_flag("admin_too", "disableadmin"))


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(DisableMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

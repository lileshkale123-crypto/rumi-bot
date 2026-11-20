"""anonadmin, adminerror, silentactions + admin cache clearing (aiogram 3.x + APScheduler)."""
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import ax_common
from ax_common import cmd_args, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_misc3")

H = ParseMode.HTML
SILENT: set[int] = set()
QUIET: set[int] = set()
_COLS = ("anon_admin", "admin_error", "silent_actions")


async def load(pool: asyncpg.Pool) -> None:
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT chat_id, anon_admin, admin_error, silent_actions FROM ax_misc_settings"
            )
        ax_common.ANON_ON.clear()
        SILENT.clear()
        QUIET.clear()
        for r in rows:
            if r["anon_admin"]:
                ax_common.ANON_ON.add(r["chat_id"])
            if r["silent_actions"]:
                SILENT.add(r["chat_id"])
            if not r["admin_error"]:
                QUIET.add(r["chat_id"])
    except Exception as e:
        log.warning("ax_misc3 load failed: %s", e)


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


def _yn(args: list[str]) -> bool | None:
    a = args[0].lower() if args else ""
    if a in ("on", "yes"):
        return True
    if a in ("off", "no"):
        return False
    return None


async def _save(pool: asyncpg.Pool, cid: int, col: str, v: bool) -> None:
    assert col in _COLS
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_misc_settings (chat_id, {col}) VALUES "
            f"($1,$2) ON CONFLICT (chat_id) DO UPDATE SET {col}=$2",
            cid,
            v,
        )


def make(col: str, name: str, store: set[int], invert: bool = False):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        g = await _gate(message, bot, pool)
        if not g:
            return
        pool, cid = g
        v = _yn(cmd_args(message))
        cur = (cid not in store) if invert else (cid in store)
        if v is None:
            await message.reply(
                f"{'✅' if cur else '🚫'} <b>{name} is {'on' if cur else 'off'}</b>\n"
                f"<blockquote>Use /{name} on or off to change it</blockquote>",
                parse_mode=H,
            )
            return
        if name == "silentactions" and v:
            async with pool.acquire() as c:
                r = await c.fetchrow("SELECT log_chat FROM ax_log_settings WHERE chat_id=$1", cid)
            if not r or not r["log_chat"]:
                await message.reply(
                    "⚠️ <b>Needs a log channel</b>\n<blockquote>Link one with /setlog first ✨</blockquote>",
                    parse_mode=H,
                )
                return
        await _save(pool, cid, col, v)
        on = (not v) if invert else v
        (store.add if on else store.discard)(cid)
        await message.reply(
            f"{'✅' if v else '🚫'} <b>{name} {'on' if v else 'off'}</b>",
            parse_mode=H,
        )

    return cmd


def clear_cache() -> None:
    ax_common._ADMIN_CACHE.clear()


router.message(Command("anonadmin"))(make("anon_admin", "anonadmin", ax_common.ANON_ON))
router.message(Command("adminerror"))(make("admin_error", "adminerror", QUIET, True))
router.message(Command("silentactions"))(make("silent_actions", "silentactions", SILENT))


async def on_startup(bot: Bot, dispatcher) -> None:
    pool: asyncpg.Pool = dispatcher["pool"]
    scheduler: AsyncIOScheduler | None = dispatcher.get("scheduler")
    await load(pool)
    if scheduler is not None:
        scheduler.add_job(
            load,
            trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=5),
            args=[pool],
            id="ax_misc3:load",
            replace_existing=True,
        )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

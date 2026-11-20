"""Owner-only watchlist: saves recent messages of watched groups for 7 days (aiogram 3.x)."""
import html
import logging
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject

from ax_adminpanel import OWNER_IDS
from ax_common import btn, cmd_args, fire

log = logging.getLogger(__name__)
router = Router(name="ax_watch")

SHOW = 20
MAX_WATCH = 3
PER = 10

_watch_ids: set[int] | None = None
_last_prune: float = 0.0
_middleware_registered = False


async def _ids(pool: asyncpg.Pool) -> set[int]:
    global _watch_ids
    if _watch_ids is None:
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT chat_id FROM ax_watchlist")
        _watch_ids = {r["chat_id"] for r in rows}
    return _watch_ids


async def _log_work(bot: Bot, pool: asyncpg.Pool, m: Message) -> None:
    u = m.from_user
    chat = m.chat
    if u is None or chat.type == "private":
        return
    ids = await _ids(pool)
    if chat.id not in ids:
        return

    body = (m.text or m.caption or "[media]")[:500]
    try:
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_watch_log (chat_id, message_id, user_id, name, body) "
                "VALUES ($1,$2,$3,$4,$5)",
                chat.id,
                m.message_id,
                u.id,
                u.first_name,
                body,
            )
    except Exception:
        log.exception("watch_log insert failed")

    title = chat.title or "Group"
    tag = (
        f"[WATCH] <b>{html.escape(title)}</b>\n"
        f"{html.escape(u.first_name or 'Member')}: {html.escape(body)}"
    )
    for owner in OWNER_IDS:
        try:
            await bot.send_message(owner, tag, parse_mode=ParseMode.HTML)
        except Exception:
            pass


class WatchMiddleware(BaseMiddleware):
    """Non-blocking watcher for watched groups."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.type != "private" and event.from_user is not None:
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                if _watch_ids is None or event.chat.id in _watch_ids:
                    fire(_log_work(bot, pool, event))
        return await handler(event, data)


async def _set(pool: asyncpg.Pool, chat_id: int, on: bool) -> bool:
    ids = await _ids(pool)
    if on and chat_id not in ids and len(ids) >= MAX_WATCH:
        return False
    async with pool.acquire() as c:
        if on:
            await c.execute(
                "INSERT INTO ax_watchlist (chat_id) VALUES ($1) ON CONFLICT DO NOTHING",
                chat_id,
            )
        else:
            await c.execute("DELETE FROM ax_watchlist WHERE chat_id=$1", chat_id)
    if on:
        ids.add(chat_id)
    else:
        ids.discard(chat_id)
    return True


async def read_view(pool: asyncpg.Pool, chat_id: int, page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    global _last_prune
    now = time.monotonic()
    async with pool.acquire() as c:
        if now - _last_prune > 3600:
            await c.execute("DELETE FROM ax_watch_log WHERE created_at < now() - interval '7 days'")
            _last_prune = now
        title = await c.fetchval("SELECT title FROM ax_groups WHERE chat_id=$1", chat_id)
        total = await c.fetchval("SELECT COUNT(*) FROM ax_watch_log WHERE chat_id=$1", chat_id)
        pages = max(1, -(-total // PER))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT name, body, created_at FROM ax_watch_log "
            "WHERE chat_id=$1 ORDER BY id DESC LIMIT $2 OFFSET $3",
            chat_id,
            PER,
            (page - 1) * PER,
        )

    watched = chat_id in await _ids(pool)
    lines = []
    for r in reversed(rows):
        t = r["created_at"].strftime("%d %b %H:%M")
        who = html.escape(r["name"] or "Member")
        txt = html.escape((r["body"] or "")[:160])
        lines.append(f"<code>{t}</code> <b>{who}</b>: {txt}")

    body = "\n".join(lines) if lines else "No saved messages yet."
    state = "watching" if watched else "not watching"
    text = (
        f"<b>{html.escape(title or 'Group')}</b>\n"
        f"<blockquote><code>{chat_id}</code> | {state} | {total} saved | kept 7 days</blockquote>\n\n"
        f"<blockquote>{body}</blockquote>\n"
        f"<i>Page {page}/{pages} (page 1 = newest)</i>"
    )

    nav = []
    if page > 1:
        nav.append(btn("Newer", f"axwz:p:{chat_id}:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("Older", f"axwz:p:{chat_id}:{page + 1}"))

    act = "off" if watched else "on"
    label = "Stop watching" if watched else "Start watching"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            nav,
            [btn("Refresh", f"axwz:p:{chat_id}:1"), btn(label, f"axwz:{act}:{chat_id}")],
            [btn("Back to group", f"axg:v:{chat_id}:1")],
        ]
    )
    return text, kb


async def _cmd(message: Message, pool: asyncpg.Pool, on: bool | None) -> None:
    u = message.from_user
    if u is None or u.id not in OWNER_IDS:
        return
    args = cmd_args(message)
    try:
        chat_id = int(args[0])
    except (IndexError, ValueError):
        await message.reply("Usage: /watch chat_id  •  /unwatch chat_id  •  /readlog chat_id")
        return
    if on is not None:
        ok = await _set(pool, chat_id, on)
        if not ok and on:
            await message.reply(f"Watchlist full (max {MAX_WATCH} groups). Unwatch one first.")
    text, kb = await read_view(pool, chat_id)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.message(Command("watch"))
async def watch_cmd(message: Message, pool: asyncpg.Pool) -> None:
    await _cmd(message, pool, True)


@router.message(Command("unwatch"))
async def unwatch_cmd(message: Message, pool: asyncpg.Pool) -> None:
    await _cmd(message, pool, False)


@router.message(Command("readlog"))
async def readlog_cmd(message: Message, pool: asyncpg.Pool) -> None:
    await _cmd(message, pool, None)


@router.callback_query(F.data.startswith("axwz:"))
async def watch_callback(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer()
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer()
        return
    act = parts[1]
    try:
        chat_id = int(parts[2])
    except ValueError:
        await query.answer()
        return

    if act == "m":
        import ax_disable
        from ax_funmode import FUN_CMDS

        async with pool.acquire() as c:
            n = await c.fetchval(
                "SELECT COUNT(*) FROM ax_disabled WHERE chat_id=$1 AND cmd = ANY($2::text[])",
                chat_id,
                FUN_CMDS,
            )
            if n >= len(FUN_CMDS):
                await c.execute(
                    "DELETE FROM ax_disabled WHERE chat_id=$1 AND cmd = ANY($2::text[])",
                    chat_id,
                    FUN_CMDS,
                )
                msg = "\U0001f50a Fun unmuted in that group."
            else:
                for cmd in FUN_CMDS:
                    await c.execute(
                        "INSERT INTO ax_disabled (chat_id, cmd) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                        chat_id,
                        cmd,
                    )
                msg = "\U0001f507 Fun muted in that group."
        ax_disable._cache.pop(chat_id, None)
        await query.answer(msg, show_alert=True)
        return

    try:
        page = int(parts[3]) if len(parts) > 3 else 1
    except ValueError:
        page = 1

    alert_msg = None
    if act in ("on", "off"):
        ok = await _set(pool, chat_id, act == "on")
        if not ok and act == "on":
            alert_msg = f"Max {MAX_WATCH} watched groups reached."

    text, kb = await read_view(pool, chat_id, page)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    await query.answer(alert_msg, show_alert=bool(alert_msg))


async def on_startup(bot: Bot, dispatcher) -> None:
    pool: asyncpg.Pool = dispatcher["pool"]
    await _ids(pool)


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(WatchMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

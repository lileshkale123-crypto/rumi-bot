"""Owner-only broadcast: copy any message to groups or DMs, with confirm and delete (aiogram 3.x)."""
import asyncio
import logging
import time

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ax_adminpanel import OWNER_IDS
from ax_common import fire

log = logging.getLogger(__name__)
router = Router(name="ax_broadcast")

H = ParseMode.HTML
_READY = [False]
_PENDING_TTL = 1800.0
# preview_msg_id -> (src_chat_id, src_msg_id, kind, markup, created_monotonic)
_pending: dict[int, tuple[int, int, str, InlineKeyboardMarkup | None, float]] = {}


async def ensure(pool: asyncpg.Pool) -> None:
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_bc_batches ("
            "id BIGSERIAL PRIMARY KEY, kind TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_bc_msgs ("
            "batch_id BIGINT NOT NULL, chat_id BIGINT NOT NULL, "
            "message_id BIGINT NOT NULL)"
        )
    _READY[0] = True


def _prune_pending(now: float) -> None:
    stale = [mid for mid, item in _pending.items() if now - item[4] > _PENDING_TTL]
    for mid in stale:
        _pending.pop(mid, None)


def _parse_buttons(m: Message) -> InlineKeyboardMarkup | None:
    text = m.text or m.caption or ""
    rows = []
    for line in text.split("\n")[1:]:
        row = []
        for part in line.split(" | "):
            if " - " not in part:
                continue
            label, url = part.rsplit(" - ", 1)
            label, url = label.strip(), url.strip()
            if url.startswith("t.me/"):
                url = "https://" + url
            if label and url.startswith(("http://", "https://", "tg://")):
                row.append(InlineKeyboardButton(text=label, url=url))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _kb(*pairs: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in pairs]]
    )


async def _start(message: Message, pool: asyncpg.Pool, kind: str) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    src = message.reply_to_message
    if src is None:
        await message.reply("Reply to the message you want to broadcast, then send the command.")
        return

    markup = _parse_buttons(message)
    nbtn = sum(len(r) for r in markup.inline_keyboard) if markup else 0
    sql = (
        "SELECT count(*) FROM ax_groups WHERE chat_id NOT IN (SELECT chat_id FROM ax_channels)"
        if kind == "g"
        else "SELECT count(*) FROM users"
    )
    async with pool.acquire() as c:
        n = await c.fetchval(sql)

    where = "groups" if kind == "g" else "DM users"
    p = await src.reply(
        "📢 <b>Broadcast preview</b>\n<blockquote>Send this message to %d %s?\nButtons: %d</blockquote>"
        % (n, where, nbtn),
        parse_mode=H,
        reply_markup=_kb(("✅ Confirm", "axb:ok"), ("❌ Cancel", "axb:no")),
    )
    now = time.monotonic()
    _prune_pending(now)
    _pending[p.message_id] = (src.chat.id, src.message_id, kind, markup, now)


@router.message(Command("broadcast"))
async def broadcast_cmd(message: Message, pool: asyncpg.Pool) -> None:
    await _start(message, pool, "g")


@router.message(Command("dmbroadcast"))
async def dmbroadcast_cmd(message: Message, pool: asyncpg.Pool) -> None:
    await _start(message, pool, "d")


async def _copy_with_retry(
    bot: Bot,
    chat_id: int,
    from_chat_id: int,
    message_id: int,
    reply_markup: InlineKeyboardMarkup | None,
):
    for attempt in range(2):
        try:
            return await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except TelegramRetryAfter as exc:
            if attempt == 1:
                raise
            await asyncio.sleep(float(exc.retry_after) + 0.5)
    return None


async def _delete_with_retry(bot: Bot, chat_id: int, message_id: int) -> None:
    for attempt in range(2):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            return
        except TelegramRetryAfter as exc:
            if attempt == 1:
                raise
            await asyncio.sleep(float(exc.retry_after) + 0.5)


async def _run_broadcast(
    bot: Bot,
    pool: asyncpg.Pool,
    status_msg: Message,
    src_chat: int,
    src_msg: int,
    kind: str,
    markup: InlineKeyboardMarkup | None,
) -> None:
    await ensure(pool)
    sql = (
        "SELECT chat_id FROM ax_groups WHERE chat_id NOT IN (SELECT chat_id FROM ax_channels)"
        if kind == "g"
        else "SELECT id AS chat_id FROM users"
    )
    async with pool.acquire() as c:
        rows = await c.fetch(sql)
        bid = await c.fetchval("INSERT INTO ax_bc_batches (kind) VALUES ($1) RETURNING id", kind)

    try:
        await status_msg.edit_text("📢 Sending to %d chats..." % len(rows))
    except Exception:
        pass

    sent, failed, saved = 0, 0, []
    for r in rows:
        cid = r["chat_id"]
        try:
            msg_id = await _copy_with_retry(bot, cid, src_chat, src_msg, markup)
            if msg_id is not None:
                saved.append((bid, cid, msg_id.message_id))
                sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    if saved:
        async with pool.acquire() as c:
            await c.executemany(
                "INSERT INTO ax_bc_msgs (batch_id, chat_id, message_id) VALUES ($1,$2,$3)",
                saved,
            )

    try:
        await status_msg.edit_text(
            "📢 <b>Broadcast done</b>\n<blockquote>Sent: %d\nFailed: %d\nTotal: %d</blockquote>"
            % (sent, failed, len(rows)),
            parse_mode=H,
            reply_markup=_kb(("🗑 Delete broadcast", "axb:del:%d" % bid)),
        )
    except Exception:
        pass


@router.callback_query(F.data == "axb:ok")
async def _ok(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer("Owner only", show_alert=True)
        return
    if query.message is None:
        await query.answer()
        return
    pend = _pending.pop(query.message.message_id, None)
    if pend is None or time.monotonic() - pend[4] > _PENDING_TTL:
        await query.answer("Expired. Send the command again.", show_alert=True)
        return
    await query.answer("Sending...")
    src_chat, src_msg, kind, markup, _ = pend
    fire(_run_broadcast(bot, pool, query.message, src_chat, src_msg, kind, markup))


@router.callback_query(F.data == "axb:no")
async def _no(query: CallbackQuery) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer("Owner only", show_alert=True)
        return
    if query.message is not None:
        _pending.pop(query.message.message_id, None)
    await query.answer()
    if query.message is not None:
        try:
            await query.message.edit_text("❌ Broadcast cancelled. Nothing was sent.")
        except Exception:
            pass


async def _run_delete(bot: Bot, pool: asyncpg.Pool, status_msg: Message, bid: int) -> None:
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT chat_id, message_id FROM ax_bc_msgs WHERE batch_id=$1", bid)
    ok = 0
    for r in rows:
        try:
            await _delete_with_retry(bot, r["chat_id"], r["message_id"])
            ok += 1
        except Exception:
            log.info("broadcast delete failed for %s", r["chat_id"])
        await asyncio.sleep(0.05)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_bc_msgs WHERE batch_id=$1", bid)
    try:
        await status_msg.edit_text(
            "🗑 <b>Broadcast deleted</b>\n<blockquote>Removed from %d of %d chats</blockquote>"
            % (ok, len(rows)),
            parse_mode=H,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("axb:del:"))
async def _del(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer("Owner only", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or query.message is None:
        await query.answer()
        return
    bid = int(parts[2])
    await query.answer("Deleting...")
    fire(_run_delete(bot, pool, query.message, bid))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

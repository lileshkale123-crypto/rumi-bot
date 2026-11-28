"""Pins: /pin /permapin /unpin /unpinall /pinned /antichannelpin /cleanlinked (aiogram 3.x)."""
import logging
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, ReplyParameters, TelegramObject

import ax_mod
from ax_common import cmd_args, esc, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_pins")

_cache: dict[int, tuple[float, dict]] = {}
_CACHE_TTL = 60.0
_CACHE_MAX = 5000
_middleware_registered = False


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX:
        return
    expired = [cid for cid, (ts, _) in _cache.items() if now - ts >= _CACHE_TTL]
    for cid in expired:
        _cache.pop(cid, None)
    if len(_cache) > _CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for cid, _ in oldest:
            _cache.pop(cid, None)


def invalidate(chat_id: int) -> None:
    _cache.pop(chat_id, None)


async def _gate(message: Message, bot: Bot) -> bool:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>",
            bot=bot,
        )
        return False
    if not await is_admin(bot, chat.id, user.id, "can_pin_messages"):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
            bot=bot,
        )
        return False
    return True


async def _settings(pool: asyncpg.Pool, chat_id: int) -> dict:
    now = time.time()
    hit = _cache.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return dict(hit[1])
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM ax_pin_settings WHERE chat_id=$1", chat_id)
    val = dict(r) if r else {"antichannelpin": False, "cleanlinked": False}
    _prune_cache(now)
    _cache[chat_id] = (now, val)
    return dict(val)


@router.message(Command("pin"))
async def pin_cmd(message: Message, bot: Bot) -> None:
    if not await _gate(message, bot):
        return
    if not message.reply_to_message:
        await ax_mod.say(
            message,
            "💫 <b>Reply needed</b>\n<blockquote>Reply to the message to pin. Add loud to notify everyone: /pin loud</blockquote>",
            bot=bot,
        )
        return
    args = cmd_args(message)
    loud = bool(args) and args[0].lower() in ("loud", "notify")
    await ax_mod.act_and_reply(
        message,
        bot.pin_chat_message(
            chat_id=message.chat.id,
            message_id=message.reply_to_message.message_id,
            disable_notification=not loud,
        ),
        "📌 <b>Pinned</b>\n<blockquote>Message pinned ✨</blockquote>",
        None,
        "Couldn't pin",
        "\n💡 I need the <b>Pin messages</b> permission",
        bot=bot,
    )


@router.message(Command("permapin"))
async def permapin_cmd(message: Message, bot: Bot) -> None:
    if not await _gate(message, bot):
        return
    parts = (message.text or "").split(None, 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    if not body:
        await ax_mod.say(
            message,
            "💫 <b>Text?</b>\n<blockquote>Example: /permapin Meeting at 8pm</blockquote>",
            bot=bot,
        )
        return
    try:
        sent = await bot.send_message(message.chat.id, esc(body), parse_mode=ParseMode.HTML)
        await bot.pin_chat_message(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except Exception as e:
        await ax_mod.say(
            message,
            f"⚠️ <b>Couldn't pin</b>\n<blockquote>{esc(str(e))}</blockquote>",
            bot=bot,
        )


@router.message(Command("unpin"))
async def unpin_cmd(message: Message, bot: Bot) -> None:
    if not await _gate(message, bot):
        return
    if message.reply_to_message:
        act = bot.unpin_chat_message(chat_id=message.chat.id, message_id=message.reply_to_message.message_id)
    else:
        act = bot.unpin_chat_message(chat_id=message.chat.id)
    await ax_mod.act_and_reply(
        message, act, "📌 <b>Unpinned</b>\n<blockquote>Done ✨</blockquote>", None, "Couldn't unpin", bot=bot
    )


@router.message(Command("unpinall"))
async def unpinall_cmd(message: Message, bot: Bot) -> None:
    if not await _gate(message, bot):
        return
    await ax_mod.act_and_reply(
        message,
        bot.unpin_all_chat_messages(chat_id=message.chat.id),
        "📌 <b>All unpinned</b>\n<blockquote>Every pinned message was removed</blockquote>",
        None,
        "Couldn't unpin",
        bot=bot,
    )


@router.message(Command("pinned"))
async def pinned_cmd(message: Message, bot: Bot) -> None:
    chat = message.chat
    if chat.type == "private":
        return
    full = await bot.get_chat(chat.id)
    p = full.pinned_message
    if not p:
        await ax_mod.say(
            message,
            "📌 <b>Nothing pinned</b>\n<blockquote>No pinned message here</blockquote>",
            bot=bot,
        )
        return
    await bot.send_message(
        chat.id,
        "📌 Pinned message:",
        reply_parameters=ReplyParameters(
            message_id=p.message_id,
            allow_sending_without_reply=True,
        ),
    )


async def _toggle(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    col: str,
    label: str,
    detail: str,
) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = cmd_args(message)
    arg = args[0].lower() if args else ""
    if arg not in ("on", "off", "yes", "no"):
        s = await _settings(pool, chat_id)
        await ax_mod.say(
            message,
            f"⚙️ <b>{label}</b>\n<blockquote>Currently <b>{'on' if s[col] else 'off'}</b>\nUse on or off</blockquote>",
            bot=bot,
        )
        return
    val = arg in ("on", "yes")
    assert col in ("antichannelpin", "cleanlinked")
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_pin_settings (chat_id, {col}) VALUES ($1,$2) "
            f"ON CONFLICT (chat_id) DO UPDATE SET {col}=$2",
            chat_id,
            val,
        )
    invalidate(chat_id)
    await ax_mod.say(
        message,
        f"✅ <b>{label} {'on' if val else 'off'}</b>\n<blockquote>{detail}</blockquote>",
        bot=bot,
    )


@router.message(Command("antichannelpin"))
async def antichannelpin_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    await _toggle(
        message,
        bot,
        pool,
        "antichannelpin",
        "Anti channel pin",
        "Telegram's auto-pin of linked channel posts is undone",
    )


@router.message(Command("cleanlinked"))
async def cleanlinked_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    await _toggle(
        message,
        bot,
        pool,
        "cleanlinked",
        "Clean linked",
        "Messages from the linked channel are deleted",
    )


class LinkedChannelMiddleware(BaseMiddleware):
    """Handles automatic forwards from linked channels (unpin and/or delete)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.is_automatic_forward:
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                s = await _settings(pool, event.chat.id)
                deleted = False
                try:
                    if s["antichannelpin"]:
                        await bot.unpin_chat_message(
                            chat_id=event.chat.id,
                            message_id=event.message_id,
                        )
                    if s["cleanlinked"]:
                        await event.delete()
                        deleted = True
                except Exception as e:
                    log.warning("linked watch failed: %s", e)
                if deleted:
                    return None
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(LinkedChannelMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

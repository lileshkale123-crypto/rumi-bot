"""Purge tools: /purge /spurge /del /purgefrom /purgeto (admins only) (aiogram 3.x)."""
import asyncio
import logging

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message

import ax_mod
from ax_common import bot_is_admin, cmd_args, fire, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_purge")

MARKS: dict[int, int] = {}  # chat_id -> message_id marked by /purgefrom
MAX_PURGE = 1000


async def _delete_later(msg: Message, delay: float = 5.0) -> None:
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


async def _check(message: Message, bot: Bot) -> bool:
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
    if not await is_admin(bot, chat.id, user.id, "can_delete_messages"):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
            bot=bot,
        )
        return False
    if not await bot_is_admin(bot, chat.id, "can_delete_messages"):
        await ax_mod.say(
            message,
            "⚠️ <b>I need admin rights</b>\n<blockquote>Give me the delete messages permission 💫</blockquote>",
            bot=bot,
        )
        return False
    return True


async def _delete_range(bot: Bot, chat_id: int, first: int, last: int) -> int:
    ids = list(range(first, last + 1))
    done = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        for attempt in range(2):
            try:
                await bot.delete_messages(chat_id, chunk)
                done += len(chunk)
                break
            except TelegramRetryAfter as exc:
                if attempt == 1:
                    break
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except Exception as e:
                log.warning("purge chunk failed: %s", e)
                break
        await asyncio.sleep(0.3)
    return done


async def _purge(message: Message, bot: Bot, silent: bool) -> None:
    if not await _check(message, bot):
        return
    chat_id = message.chat.id
    if not message.reply_to_message:
        await ax_mod.say(
            message,
            "💫 <b>Reply needed</b>\n<blockquote>Reply to the first message you want gone.\n"
            "Or: /purge 20 as a reply to delete 20 messages from there.</blockquote>",
            bot=bot,
        )
        return

    first = message.reply_to_message.message_id
    last = message.message_id
    args = cmd_args(message)
    count_mode = bool(args and args[0].isdigit())
    if count_mode:
        last = min(message.message_id, first + max(1, int(args[0])) - 1)

    if last - first + 1 > MAX_PURGE:
        await ax_mod.say(
            message,
            f"⚠️ <b>Too many</b>\n<blockquote>I purge up to {MAX_PURGE} messages at once</blockquote>",
            bot=bot,
        )
        return

    done = await _delete_range(bot, chat_id, first, last)
    if count_mode and last < message.message_id:
        try:
            await message.delete()
        except Exception:
            pass
    if silent:
        return

    note = await bot.send_message(
        chat_id,
        f"🧹 <b>Purge complete</b>\n<blockquote>Cleared <b>{done}</b> messages ✨</blockquote>",
        parse_mode=ParseMode.HTML,
    )
    fire(_delete_later(note, 5.0))


@router.message(Command("purge"))
async def purge_cmd(message: Message, bot: Bot) -> None:
    await _purge(message, bot, False)


@router.message(Command("spurge"))
async def spurge_cmd(message: Message, bot: Bot) -> None:
    await _purge(message, bot, True)


@router.message(Command("del"))
async def del_cmd(message: Message, bot: Bot) -> None:
    if not await _check(message, bot):
        return
    if not message.reply_to_message:
        await ax_mod.say(
            message,
            "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want removed</blockquote>",
            bot=bot,
        )
        return
    for m in (message.reply_to_message, message):
        try:
            await m.delete()
        except Exception:
            pass


@router.message(Command("purgefrom"))
async def purgefrom_cmd(message: Message, bot: Bot) -> None:
    if not await _check(message, bot):
        return
    if not message.reply_to_message:
        await ax_mod.say(
            message,
            "💫 <b>Reply needed</b>\n<blockquote>Reply to the message where the purge should start</blockquote>",
            bot=bot,
        )
        return
    MARKS[message.chat.id] = message.reply_to_message.message_id
    await ax_mod.say(
        message,
        "📍 <b>Start marked</b>\n<blockquote>Now reply to the last message with /purgeto</blockquote>",
        bot=bot,
    )


@router.message(Command("purgeto"))
async def purgeto_cmd(message: Message, bot: Bot) -> None:
    if not await _check(message, bot):
        return
    chat_id = message.chat.id
    start = MARKS.get(chat_id)
    if start is None:
        await ax_mod.say(
            message,
            "💫 <b>No start mark</b>\n<blockquote>Use /purgefrom on the first message first</blockquote>",
            bot=bot,
        )
        return
    if not message.reply_to_message:
        await ax_mod.say(
            message,
            "💫 <b>Reply needed</b>\n<blockquote>Reply to the last message you want deleted</blockquote>",
            bot=bot,
        )
        return
    end = message.reply_to_message.message_id
    if end < start or end - start + 1 > MAX_PURGE:
        await ax_mod.say(
            message,
            f"⚠️ <b>Bad range</b>\n<blockquote>The end must come after the start, within {MAX_PURGE} messages</blockquote>",
            bot=bot,
        )
        return

    done = await _delete_range(bot, chat_id, start, end)
    MARKS.pop(chat_id, None)
    try:
        await message.delete()
    except Exception:
        pass
    note = await bot.send_message(
        chat_id,
        f"🧹 <b>Purge complete</b>\n<blockquote>Cleared <b>{done}</b> messages ✨</blockquote>",
        parse_mode=ParseMode.HTML,
    )
    fire(_delete_later(note, 5.0))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

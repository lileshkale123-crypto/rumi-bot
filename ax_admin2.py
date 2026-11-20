"""/adminlist /admincache /id (aiogram 3.x)."""
import logging

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import Message

import ax_common
import ax_mod
from ax_common import cmd_args, esc, resolve_target

log = logging.getLogger(__name__)
router = Router(name="ax_admin2")


def _mention(user) -> str:
    return f'<a href="tg://user?id={user.id}">{esc(user.first_name or "Admin")}</a>'


async def _fetch_admins(bot: Bot, chat_id: int):
    return await bot.get_chat_administrators(chat_id)


@router.message(Command("adminlist"))
async def adminlist_cmd(message: Message, bot: Bot) -> None:
    chat = message.chat
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside a group 💫</blockquote>",
            bot=bot,
        )
        return
    try:
        admins = await _fetch_admins(bot, chat.id)
    except Exception as e:
        await ax_mod.say(
            message,
            f"⚠️ <b>Couldn't load admins</b>\n<blockquote>{esc(str(e))}</blockquote>",
            bot=bot,
        )
        return

    owner = [a for a in admins if a.status == ChatMemberStatus.CREATOR]
    humans = [a for a in admins if a.status != ChatMemberStatus.CREATOR and not a.user.is_bot]
    bots = [a for a in admins if a.user.is_bot]

    lines = []
    for a in owner:
        lines.append(f"👑 {_mention(a.user)}")
    for a in humans:
        title = f" • <i>{esc(a.custom_title)}</i>" if getattr(a, "custom_title", None) else ""
        lines.append(f"🛡️ {_mention(a.user)}{title}")
    for a in bots:
        lines.append(f"🤖 {esc(a.user.first_name or 'Bot')}")

    await ax_mod.say(
        message,
        (
            f"👮 <b>Admins of {esc(chat.title or 'this group')}</b>\n"
            f"<blockquote>{chr(10).join(lines)}</blockquote>"
        ),
        bot=bot,
    )


@router.message(Command("admincache"))
async def admincache_cmd(message: Message, bot: Bot) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside a group 💫</blockquote>",
            bot=bot,
        )
        return
    try:
        admins = await _fetch_admins(bot, chat.id)
    except Exception as e:
        await ax_mod.say(
            message,
            f"⚠️ <b>Couldn't refresh</b>\n<blockquote>{esc(str(e))}</blockquote>",
            bot=bot,
        )
        return
    if user.id not in {a.user.id for a in admins}:
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
            bot=bot,
        )
        return

    # Actually refresh the cached admin entries for this chat
    ax_common.invalidate(chat.id)
    for a in admins:
        ax_common.remember(chat.id, a)

    await ax_mod.say(
        message,
        f"🔄 <b>Admin list refreshed</b>\n<blockquote>Found <b>{len(admins)}</b> admins right now ✨</blockquote>",
        bot=bot,
    )


@router.message(Command("id"))
async def id_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    target = None
    if message.reply_to_message or cmd_args(message):
        target = await resolve_target(message, bot, pool)
    who = target or user
    lines = [f"👤 <b>{esc(who.first_name or 'User')}:</b> <code>{who.id}</code>"]
    if chat.type != "private":
        lines.append(f"💬 <b>Group:</b> <code>{chat.id}</code>")
    fwd = (
        getattr(message.reply_to_message, "forward_origin", None)
        if message.reply_to_message
        else None
    )
    sender = getattr(fwd, "sender_user", None)
    if sender:
        lines.append(
            f"↪️ <b>Forwarded from {esc(sender.first_name or 'User')}:</b> <code>{sender.id}</code>"
        )
    await ax_mod.say(message, f"🆔 <b>IDs</b>\n<blockquote>{chr(10).join(lines)}</blockquote>", bot=bot)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

"""Extra ban/mute/kick variants: d* (delete msg), s* (silent), kickme (aiogram 3.x)."""
import logging

import asyncpg
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, Message

import ax_mod
from ax_common import btn, fire, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_mod2")


async def _del(msg: Message | None) -> None:
    if msg is None:
        return
    try:
        await msg.delete()
    except Exception:
        pass


def make_variant(kind: str, mode: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        if mode == "d" and not message.reply_to_message:
            await ax_mod.say(
                message,
                "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want removed</blockquote>",
                bot=bot,
            )
            return

        target = await ax_mod.gate(message, bot, pool, kind in ("mute", "ban", "kick"))
        if target is None:
            return

        chat_id = message.chat.id
        try:
            await ax_mod.perform(kind, bot, pool, chat_id, target.id)
        except Exception as e:
            await ax_mod.say(
                message,
                f"⚠️ <b>Couldn't do that</b>\n<blockquote>{ax_mod.esc(str(e))}</blockquote>",
                bot=bot,
            )
            return

        fire(ax_mod._log_action(bot, pool, chat_id, message.from_user, f"{mode}{kind}", target))

        if mode == "d":
            await _del(message.reply_to_message)
            await _del(message)
        elif mode == "s":
            await _del(message)
            return

        icon, title = ax_mod.META[kind]
        kb = None
        if kind in ax_mod.UNDO:
            k, label = ax_mod.UNDO[kind]
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[btn(label, f"axm:{k}:{target.id}", style="success")]]
            )
        await ax_mod.say(message, ax_mod.card(icon, title, target.first_name or "User"), kb, bot=bot)

    return cmd


@router.message(Command("kickme"))
async def kickme_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    chat = message.chat
    if user is None:
        return
    if chat.type == "private":
        await message.reply("Use this inside a group 💜")
        return
    if await is_admin(bot, chat.id, user.id):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins stay</b>\n<blockquote>I can't kick admins ✨</blockquote>",
            bot=bot,
        )
        return
    try:
        await ax_mod.perform("kick", bot, pool, chat.id, user.id)
    except Exception as e:
        await ax_mod.say(
            message,
            f"⚠️ <b>Couldn't do that</b>\n<blockquote>{ax_mod.esc(str(e))}</blockquote>",
            bot=bot,
        )
        return
    await ax_mod.say(
        message,
        f"👢 <b>{ax_mod.esc(user.first_name or 'Member')}</b> kicked themselves out. Bye! 💜",
        bot=bot,
    )


for _kind in ("ban", "mute", "kick"):
    router.message(Command("d" + _kind))(make_variant(_kind, "d"))
    router.message(Command("s" + _kind))(make_variant(_kind, "s"))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

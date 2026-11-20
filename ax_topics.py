"""Forum topic tools: /newtopic /renametopic /closetopic /reopentopic /deletetopic (aiogram 3.x)."""
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from ax_common import cmd_args, esc, is_admin

router = Router(name="ax_topics")
H = ParseMode.HTML


async def _say(message: Message, text: str):
    return await message.reply(text, parse_mode=H)


async def _guard(message: Message, bot: Bot, need_topic: bool) -> tuple[bool, int | None]:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False, None
    if chat.type != "supergroup" or not getattr(chat, "is_forum", False):
        await _say(
            message,
            "<b>Topics only</b>\n<blockquote>This works in groups that have Topics turned on ✨</blockquote>",
        )
        return False, None
    if not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await _say(
            message,
            "🔒 <b>Admins only</b>\n<blockquote>Only admins can manage topics ✨</blockquote>",
        )
        return False, None
    tid = message.message_thread_id if message.is_topic_message else None
    if need_topic and not tid:
        await _say(
            message,
            "📌 <b>Use it inside a topic</b>\n<blockquote>Send this command in the topic you want to change 💫</blockquote>",
        )
        return False, None
    return True, tid


async def _fail(message: Message, e: Exception) -> None:
    await _say(
        message,
        "⚠️ <b>Could not do that</b>\n<blockquote>Make sure I am admin with the Manage Topics right. %s</blockquote>"
        % esc(str(e)[:100]),
    )


@router.message(Command("newtopic"))
async def newtopic_cmd(message: Message, bot: Bot) -> None:
    ok, _ = await _guard(message, bot, False)
    if not ok:
        return
    name = " ".join(cmd_args(message)).strip()
    if not name:
        await _say(
            message,
            "✍️ <b>Give it a name</b>\n<blockquote>Example: /newtopic Memes and fun</blockquote>",
        )
        return
    try:
        await bot.create_forum_topic(chat_id=message.chat.id, name=name[:128])
    except Exception as e:
        await _fail(message, e)
        return
    await _say(message, "✅ <b>Topic created</b>\n<blockquote>%s is ready 💫</blockquote>" % esc(name[:128]))


@router.message(Command("renametopic"))
async def renametopic_cmd(message: Message, bot: Bot) -> None:
    ok, tid = await _guard(message, bot, True)
    if not ok or tid is None:
        return
    name = " ".join(cmd_args(message)).strip()
    if not name:
        await _say(
            message,
            "✍️ <b>Give it a new name</b>\n<blockquote>Example: /renametopic Chill zone</blockquote>",
        )
        return
    try:
        await bot.edit_forum_topic(chat_id=message.chat.id, message_thread_id=tid, name=name[:128])
    except Exception as e:
        await _fail(message, e)
        return
    await _say(
        message,
        "✅ <b>Renamed</b>\n<blockquote>This topic is now %s 💫</blockquote>" % esc(name[:128]),
    )


@router.message(Command("closetopic"))
async def closetopic_cmd(message: Message, bot: Bot) -> None:
    ok, tid = await _guard(message, bot, True)
    if not ok or tid is None:
        return
    try:
        await bot.close_forum_topic(chat_id=message.chat.id, message_thread_id=tid)
    except Exception as e:
        await _fail(message, e)
        return
    await _say(
        message,
        "🔒 <b>Topic closed</b>\n<blockquote>Only admins can write here now ✨</blockquote>",
    )


@router.message(Command("reopentopic"))
async def reopentopic_cmd(message: Message, bot: Bot) -> None:
    ok, tid = await _guard(message, bot, True)
    if not ok or tid is None:
        return
    try:
        await bot.reopen_forum_topic(chat_id=message.chat.id, message_thread_id=tid)
    except Exception as e:
        await _fail(message, e)
        return
    await _say(
        message,
        "🔓 <b>Topic reopened</b>\n<blockquote>Everyone can write here again 💫</blockquote>",
    )


@router.message(Command("deletetopic"))
async def deletetopic_cmd(message: Message, bot: Bot) -> None:
    ok, tid = await _guard(message, bot, True)
    if not ok or tid is None:
        return
    try:
        await bot.delete_forum_topic(chat_id=message.chat.id, message_thread_id=tid)
    except Exception as e:
        await _fail(message, e)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

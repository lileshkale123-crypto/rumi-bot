"""Small info commands: runs, limits, privacy, markdownhelp, donate (aiogram 3.x)."""
import random

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from ax_common import esc

router = Router(name="ax_extras")
H = ParseMode.HTML

RUNS = [
    "{n} tiptoes away with a suspicious cookie 🍪",
    "{n} vanishes in a puff of glitter ✨",
    "{n} sprints off to hide behind the sofa 🛋️",
    "{n} slides away on a banana peel 🍌",
    "{n} escapes through the group's secret door 🚪",
    "{n} runs off to chase a rainbow 🌈",
]


async def _say(message: Message, text: str):
    return await message.reply(text, parse_mode=H)


@router.message(Command("runs"))
async def runs_cmd(message: Message) -> None:
    user = message.from_user
    name = "<b>%s</b>" % esc((user.first_name if user else None) or "Someone")
    await _say(message, random.choice(RUNS).replace("{n}", name))


@router.message(Command("limits"))
async def limits_cmd(message: Message) -> None:
    await _say(
        message,
        (
            "📏 <b>Limits</b>\n"
            "<blockquote>Telegram sets most of these, not Rumi ✨</blockquote>\n\n"
            "• A text message holds up to 4096 characters\n"
            "• A photo or video caption is usually up to 1024 characters\n"
            "• A message can carry up to 100 buttons, 8 per row\n"
            "• Note names are cut at 50 characters\n\n"
            "<blockquote>If a long note fails to send, split it into two notes 💫</blockquote>"
        ),
    )


@router.message(Command("privacy"))
async def privacy_cmd(message: Message) -> None:
    await _say(
        message,
        (
            "🔐 <b>Privacy</b>\n"
            "<blockquote>Here is what Rumi keeps so its features can work ✨</blockquote>\n\n"
            "• Your Telegram id, first name and username, for your wallet and rankings\n"
            "• Each group's settings, notes, filters, warns and rules\n"
            "• Short AI chat memory for the chat where you talk to Rumi\n\n"
            "Rumi never asks for your phone number or passwords.\n"
            "<blockquote>Want your data removed? Ask the bot owner 💫</blockquote>"
        ),
    )


@router.message(Command("markdownhelp"))
async def markdownhelp_cmd(message: Message) -> None:
    await _say(
        message,
        (
            "🖋️ <b>Formatting guide</b>\n"
            "<blockquote>Rumi reads these tags in notes, filters and welcome texts ✨</blockquote>\n\n"
            "&lt;b&gt;bold&lt;/b&gt; → <b>bold</b>\n"
            "&lt;i&gt;italic&lt;/i&gt; → <i>italic</i>\n"
            "&lt;u&gt;underline&lt;/u&gt; → <u>underline</u>\n"
            "&lt;s&gt;strike&lt;/s&gt; → <s>strike</s>\n"
            "&lt;code&gt;code&lt;/code&gt; → <code>code</code>\n"
            "&lt;tg-spoiler&gt;secret&lt;/tg-spoiler&gt; → <tg-spoiler>secret</tg-spoiler>\n"
            '&lt;a href="https://example.com"&gt;link&lt;/a&gt; → <a href="https://example.com">link</a>\n'
            "&lt;blockquote&gt;quote&lt;/blockquote&gt; → quote box\n\n"
            "<blockquote>Buttons, fillings and tags are explained in /help → Notes 💫</blockquote>"
        ),
    )


async def donate_cmd(message: Message) -> None:
    await _say(
        message,
        (
            "💖 <b>Support Rumi</b>\n"
            "<blockquote>Rumi is free to use ✨</blockquote>\n\n"
            "The best gift is sharing: add Rumi to a friend's group, "
            "tell your friends, and send feedback to the owner."
        ),
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

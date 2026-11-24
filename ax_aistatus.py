"""/aistatus (owner only): tests every AI model and shows why Rumi says 'my brain is busy'."""
import html
import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import ai_engine
from ax_adminpanel import OWNER_IDS

log = logging.getLogger(__name__)
router = Router(name="ax_aistatus")


@router.message(Command("aistatus"))
async def aistatus_cmd(message: Message) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    wait = await message.reply("Testing AI models...")
    try:
        lines = await ai_engine.status()
    except Exception as exc:
        lines = ["status failed: %s" % exc]
    text = "<b>AI STATUS</b>\n<pre>" + html.escape("\n".join(lines))[:3600] + "</pre>"
    try:
        await wait.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        await message.reply(text, parse_mode=ParseMode.HTML)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

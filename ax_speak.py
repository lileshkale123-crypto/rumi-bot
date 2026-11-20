"""/speak: text to voice using edge-tts (free, no key, no AI quota) (aiogram 3.x)."""
import io
import logging
import re
import time

import edge_tts
from aiogram import Bot, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from ax_common import cmd_args

log = logging.getLogger(__name__)
router = Router(name="ax_speak")

MAX_CHARS = 300
COOLDOWN_SECONDS = 20
VOICE_HI = "hi-IN-SwaraNeural"
VOICE_EN = "en-US-JennyNeural"

_last_use: dict[int, float] = {}
_LAST_MAX = 5000
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def _prune_cooldowns(now: float) -> None:
    if len(_last_use) <= _LAST_MAX:
        return
    stale = [uid for uid, ts in _last_use.items() if now - ts >= COOLDOWN_SECONDS * 2]
    for uid in stale:
        _last_use.pop(uid, None)


@router.message(Command("speak"))
async def speak_cmd(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    text = " ".join(cmd_args(message)).strip()
    if not text and message.reply_to_message:
        r = message.reply_to_message
        text = (r.text or r.caption or "").strip()

    if not text:
        await message.reply(
            "🎙️ <b>RUMI VOICE</b>\n\n"
            "<blockquote>Type <code>/speak your text</code>\n"
            "or reply to a message with <code>/speak</code></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    if len(text) > MAX_CHARS:
        await message.reply(
            f"⚠️ <b>Too long!</b> Keep it under {MAX_CHARS} characters.",
            parse_mode=ParseMode.HTML,
        )
        return

    now = time.monotonic()
    _prune_cooldowns(now)
    wait = COOLDOWN_SECONDS - (now - _last_use.get(user.id, -1e9))
    if wait > 0:
        await message.reply(
            f"⏳ <b>Voice is resting.</b> Try again in {int(wait) + 1}s.",
            parse_mode=ParseMode.HTML,
        )
        return
    _last_use[user.id] = now

    voice = VOICE_HI if _DEVANAGARI.search(text) else VOICE_EN
    try:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VOICE)
        except Exception:
            pass
        buf = io.BytesIO()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        if buf.tell() == 0:
            raise RuntimeError("no audio returned")
        await message.reply_voice(
            voice=BufferedInputFile(buf.getvalue(), filename="rumi.mp3")
        )
    except Exception:
        log.exception("speak failed")
        _last_use.pop(user.id, None)
        await message.reply(
            "😿 <b>My voice slipped.</b> Please try again in a moment.",
            parse_mode=ParseMode.HTML,
        )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

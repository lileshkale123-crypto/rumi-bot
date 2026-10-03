"""/speak: text to voice using edge-tts (free, no key, no AI quota)."""
import io
import logging
import re
import time

import edge_tts
from telegram.constants import ChatAction
from telegram.ext import CommandHandler

log = logging.getLogger(__name__)

MAX_CHARS = 300
COOLDOWN_SECONDS = 20
VOICE_HI = "hi-IN-SwaraNeural"
VOICE_EN = "en-US-JennyNeural"

_last_use = {}
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


async def speak_cmd(update, context):
    msg = update.effective_message
    user = update.effective_user
    text = " ".join(context.args).strip() if context.args else ""
    if not text and msg.reply_to_message:
        r = msg.reply_to_message
        text = (r.text or r.caption or "").strip()

    if not text:
        await msg.reply_html(
            "🎙️ <b>RUMI VOICE</b>\n\n"
            "<blockquote>Type <code>/speak your text</code>\n"
            "or reply to a message with <code>/speak</code></blockquote>"
        )
        return
    if len(text) > MAX_CHARS:
        await msg.reply_html(f"⚠️ <b>Too long!</b> Keep it under {MAX_CHARS} characters.")
        return

    now = time.monotonic()
    wait = COOLDOWN_SECONDS - (now - _last_use.get(user.id, -1e9))
    if wait > 0:
        await msg.reply_html(f"⏳ <b>Voice is resting.</b> Try again in {int(wait) + 1}s.")
        return
    _last_use[user.id] = now

    voice = VOICE_HI if _DEVANAGARI.search(text) else VOICE_EN
    try:
        await msg.chat.send_action(ChatAction.RECORD_VOICE)
        buf = io.BytesIO()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        if buf.tell() == 0:
            raise RuntimeError("no audio returned")
        buf.seek(0)
        buf.name = "rumi.mp3"
        await msg.reply_voice(voice=buf)
    except Exception:
        log.exception("speak failed")
        _last_use.pop(user.id, None)
        await msg.reply_html("😿 <b>My voice slipped.</b> Please try again in a moment.")


def register(app):
    app.add_handler(CommandHandler("speak", speak_cmd))

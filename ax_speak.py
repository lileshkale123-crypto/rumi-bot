"""/voice (also /speak): text to voice using edge-tts (free, no key) (aiogram 3.x).

Works on typed text and on a replied message. Options, in any order before the text:
  hi / en      language   (default: Hindi voice for Devanagari, Indian English for the rest)
  male / female voice     (default: female)
Examples: /voice hello there    /voice hi male namaste    (reply) /voice male
The result is sent as a real voice note (ogg/opus through ffmpeg); if ffmpeg is missing or fails,
the mp3 is sent instead.
"""
import asyncio
import html
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

MAX_CHARS = 500
COOLDOWN_SECONDS = 10
TTS_TIMEOUT = 25.0
TTS_TRIES = 3

# (language, gender) -> edge-tts voice
VOICES = {
    ("hi", "f"): "hi-IN-SwaraNeural",
    ("hi", "m"): "hi-IN-MadhurNeural",
    ("en", "f"): "en-IN-NeerjaNeural",
    ("en", "m"): "en-IN-PrabhatNeural",
}
_LANG_FLAGS = {"hi": "hi", "hindi": "hi", "en": "en", "english": "en"}
_GENDER_FLAGS = {
    "male": "m", "m": "m", "boy": "m", "man": "m",
    "female": "f", "f": "f", "girl": "f", "woman": "f",
}

_last_use: dict[int, float] = {}
_LAST_MAX = 5000
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D]")
_HTML_MD = re.compile(r"[*_`~|<>]")


def _prune_cooldowns(now: float) -> None:
    if len(_last_use) <= _LAST_MAX:
        return
    stale = [uid for uid, ts in _last_use.items() if now - ts >= COOLDOWN_SECONDS * 2]
    for uid in stale:
        _last_use.pop(uid, None)


def parse_options(args: list[str]):
    """Split leading option words from the text. Returns (lang or None, gender, rest_words)."""
    lang, gender = None, "f"
    i = 0
    while i < len(args):
        w = args[i].lower()
        if w in _LANG_FLAGS and lang is None:
            lang = _LANG_FLAGS[w]
        elif w in _GENDER_FLAGS:
            gender = _GENDER_FLAGS[w]
        else:
            break
        i += 1
    return lang, gender, args[i:]


def clean(text: str) -> str:
    text = _URL.sub(" ", text)
    text = _EMOJI.sub(" ", text)
    text = _HTML_MD.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _chunks(text: str, limit: int = 180) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > limit:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    return out


async def _google_tts(text: str, lang: str) -> bytes:
    """Backup voice (one female voice) when the main service refuses this server."""
    import httpx

    host = "translate.google.co.in" if lang == "en" else "translate.google.com"
    parts = _chunks(text)
    data = b""
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "Mozilla/5.0"}) as cli:
        for i, part in enumerate(parts):
            r = await cli.get(
                "https://%s/translate_tts" % host,
                params={
                    "ie": "UTF-8", "q": part, "tl": lang, "client": "tw-ob",
                    "total": len(parts), "idx": i, "textlen": len(part),
                },
            )
            if r.status_code != 200 or not r.content:
                raise RuntimeError("google tts http %s" % r.status_code)
            data += r.content
    return data


async def _edge(text: str, voice: str) -> bytes:
    buf = io.BytesIO()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    if not buf.tell():
        raise RuntimeError("no audio returned")
    return buf.getvalue()


async def _synth(text: str, voice: str, lang: str = "en") -> bytes:
    errors = []
    for attempt in range(2):
        try:
            return await asyncio.wait_for(_edge(text, voice), TTS_TIMEOUT)
        except Exception as exc:  # network, timeout, service refused this server
            errors.append("edge: %s %s" % (type(exc).__name__, str(exc)[:60]))
            log.warning("edge-tts try %d failed: %r", attempt + 1, exc)
        await asyncio.sleep(0.5)
    try:
        return await asyncio.wait_for(_google_tts(text, lang), 40.0)
    except Exception as exc:
        errors.append("google: %s %s" % (type(exc).__name__, str(exc)[:60]))
        log.warning("google tts failed: %r", exc)
    raise RuntimeError(" | ".join(errors))


async def _to_ogg(mp3: bytes) -> bytes | None:
    """mp3 -> ogg/opus voice note. None when ffmpeg is missing or fails."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
            "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(mp3), 30.0)
        return out if proc.returncode == 0 and out else None
    except Exception:
        return None


@router.message(Command("voice", "speak"))
async def voice_cmd(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    lang, gender, words = parse_options(cmd_args(message))
    text = " ".join(words).strip()
    if not text and message.reply_to_message:
        r = message.reply_to_message
        text = (r.text or r.caption or "").strip()
    text = clean(text)

    if not text:
        await message.reply(
            "\U0001F399\uFE0F <b>VOICE</b>\n\n"
            "<blockquote>Type <code>/voice your text</code>\n"
            "or reply to a message with <code>/voice</code>\n\n"
            "Options: <code>hi</code> <code>en</code> <code>male</code> <code>female</code>\n"
            "Example: <code>/voice hi male namaste</code></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    if len(text) > MAX_CHARS:
        await message.reply(
            f"\u26A0\uFE0F <b>Too long!</b> Keep it under {MAX_CHARS} characters.",
            parse_mode=ParseMode.HTML,
        )
        return

    now = time.monotonic()
    _prune_cooldowns(now)
    wait = COOLDOWN_SECONDS - (now - _last_use.get(user.id, -1e9))
    if wait > 0:
        await message.reply(
            f"\u23F3 <b>Voice is resting.</b> Try again in {int(wait) + 1}s.",
            parse_mode=ParseMode.HTML,
        )
        return
    _last_use[user.id] = now

    if lang is None:
        lang = "hi" if _DEVANAGARI.search(text) else "en"
    voice = VOICES[(lang, gender)]
    try:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VOICE)
        except Exception:
            pass
        mp3 = await _synth(text, voice, lang)
        ogg = await _to_ogg(mp3)
        if ogg:
            await message.reply_voice(voice=BufferedInputFile(ogg, filename="voice.ogg"))
        else:
            await message.reply_voice(voice=BufferedInputFile(mp3, filename="voice.mp3"))
    except Exception as exc:
        log.exception("voice failed")
        _last_use.pop(user.id, None)
        await message.reply(
            "\U0001F63F <b>My voice slipped.</b> Please try again in a moment.\n"
            "<i>" + html.escape(str(exc)[:160]) + "</i>",
            parse_mode=ParseMode.HTML,
        )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

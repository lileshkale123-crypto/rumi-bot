"""/kang: copy a sticker, GIF, photo, or text message into the user's own sticker pack (aiogram 3.x)."""
import asyncio
import html
import io
import logging
import os
import shutil
import subprocess
import tempfile

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputSticker, Message
from PIL import Image

import quote_emoji as qe
from ax_common import btn, cmd_args, get_bot_username, run_heavy

log = logging.getLogger(__name__)
router = Router(name="ax_kang")

HELP = (
    "🧷 <b>KANG</b>\n<blockquote>Reply to a sticker or photo with "
    "<code>/kang</code>\nOptional: <code>/kang 😊</code> picks the emoji.</blockquote>"
)

_known: dict[str, int] = {}  # pack name -> sticker count (skips "does pack exist" call)
_locks: dict[int, asyncio.Lock] = {}  # user id -> Lock, used only while creating a pack


async def _retry(fn, *a, **kw):
    for i in range(3):
        try:
            return await fn(*a, **kw)
        except TelegramBadRequest:
            raise
        except TelegramRetryAfter as e:
            ra = float(e.retry_after)
            await asyncio.sleep(min(ra + 1.0, 10.0))
        except TelegramNetworkError:
            await asyncio.sleep(1 + i)
    return await fn(*a, **kw)


def _source(msg: Message):
    r = msg.reply_to_message
    if not r:
        return None
    s = r.sticker
    if s:
        if s.type != "regular":
            return None
        kind = "animated" if s.is_animated else "video" if s.is_video else "static"
        ext = {"animated": "tgs", "video": "webm", "static": "webp"}[kind]
        return kind, s.file_id, s.emoji or "🙂", ext
    if r.animation:
        return "video", r.animation.file_id, "\U0001F602", "gif"
    if r.photo:
        return "static", r.photo[-1].file_id, "🙂", "png"
    d = r.document
    if d and d.mime_type and d.mime_type.startswith("image/"):
        return "static", d.file_id, "🙂", "png"
    return None


def _to_png(data: bytes) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = im.size
    k = 512 / max(w, h)
    im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG", compress_level=3)
    if out.tell() > 500_000:
        out = io.BytesIO()
        im.save(out, "WEBP", quality=85)
    return out.getvalue()


def _gif_to_webm(data: bytes) -> bytes:
    d = tempfile.mkdtemp()
    try:
        src = os.path.join(d, "in.bin")
        dst = os.path.join(d, "out.webm")
        with open(src, "wb") as fh:
            fh.write(data)
        vf = "fps=30,scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos,format=yuva420p"
        for crf in ("38", "46", "55"):
            cmd = [
                "ffmpeg", "-y", "-i", src, "-t", "3", "-an", "-vf", vf,
                "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", crf, dst,
            ]
            subprocess.run(cmd, capture_output=True, timeout=90, check=True)
            with open(dst, "rb") as fh:
                out = fh.read()
            if len(out) <= 256_000:
                return out
        raise ValueError("gif too big")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _fit_static(data: bytes) -> bytes:
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    ok = im.format in ("PNG", "WEBP")
    if ok and max(w, h) == 512 and w <= 512 and h <= 512 and len(data) <= 500_000:
        return data
    return _to_png(data)


def _render_text_png(dicts: list[dict]) -> bytes:
    import quote_pil as qp

    img = qp.render_messages(dicts, None)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _text_source(message: Message, bot: Bot):
    r = message.reply_to_message
    if not r or not (r.text or r.caption):
        return None
    try:
        import ax_quote as aq

        roles = await aq._roles(bot, message.chat.id, [r])
        dicts = await aq._build_msg_dicts(bot, [r], roles, include_reply=False)
        if not dicts:
            return None
        png_bytes = await run_heavy(_render_text_png, dicts)
        return "static", png_bytes, "\U0001F642", "png"
    except Exception:
        log.exception("kang text failed")
        return None


def _set_missing(e: Exception) -> bool:
    t = str(e).lower()
    return "stickerset_invalid" in t or "sticker set not found" in t or "stickerset not found" in t


async def _push_inner(bot: Bot, uid: int, name: str, title: str, st: InputSticker):
    """Add a sticker, creating the pack if needed. Returns sticker count."""
    try:
        await _retry(
            bot.add_sticker_to_set,
            user_id=uid,
            name=name,
            sticker=st,
            request_timeout=60,
        )
        if name in _known:
            _known[name] += 1
            return _known[name]
    except TelegramBadRequest as e:
        if not _set_missing(e):
            raise
        await _retry(
            bot.create_new_sticker_set,
            user_id=uid,
            name=name,
            title=title,
            stickers=[st],
            request_timeout=60,
        )
        _known[name] = 1
        return 1
    try:
        _known[name] = len((await bot.get_sticker_set(name)).stickers)
    except Exception:
        return "?"
    return _known[name]


async def _push(bot: Bot, uid: int, name: str, title: str, st: InputSticker):
    # Fast path: no waiting. Lock only until the pack is known (first time),
    # so two parallel kangs can't both try to create the same pack.
    if name in _known:
        return await _push_inner(bot, uid, name, title, st)
    async with _locks.setdefault(uid, asyncio.Lock()):
        return await _push_inner(bot, uid, name, title, st)


async def _download(bot: Bot, fid: str) -> bytes:
    f = await _retry(bot.get_file, fid)
    buf = io.BytesIO()
    await _retry(bot.download_file, f.file_path, destination=buf)
    return buf.getvalue()


async def _say(msg: Message, status: Message | None, text: str, kb: InlineKeyboardMarkup | None = None):
    if status is not None:
        return await status.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    return await msg.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _run(msg: Message, u, bot: Bot, kind: str, fid, emoji: str, ext: str, status: Message | None) -> None:
    bot_username = await get_bot_username(bot)
    name = f"k{u.id}_{kind}_by_{bot_username}"
    title = f"{u.first_name or 'My'}'s {kind} pack"[:64]

    try:
        count = None
        if isinstance(fid, str) and ext in ("tgs", "webm", "webp") and msg.reply_to_message and msg.reply_to_message.sticker:
            # real sticker: reuse Telegram's own file, no download/upload
            try:
                count = await _push(
                    bot,
                    u.id,
                    name,
                    title,
                    InputSticker(sticker=fid, emoji_list=[emoji], format=kind),
                )
            except TelegramBadRequest as e:
                t = str(e).lower()
                if (
                    "peer_id" in t
                    or "user_id" in t
                    or "user not found" in t
                    or "too much" in t
                    or "too many" in t
                ):
                    raise
        if count is None:
            if isinstance(fid, (bytes, bytearray)):
                data = bytes(fid)
            else:
                data = await _download(bot, fid)
            if ext == "gif":
                data = await asyncio.to_thread(_gif_to_webm, data)
                ext = "webm"
            if kind == "static":
                data = await asyncio.to_thread(_fit_static, data)
                ext = "webp" if data[:4] == b"RIFF" else "png"
            st = InputSticker(
                sticker=BufferedInputFile(data, filename=f"s.{ext}"),
                emoji_list=[emoji],
                format=kind,
            )
            count = await _push(bot, u.id, name, title, st)
    except TelegramAPIError as e:
        log.warning("kang failed: %s: %s", type(e).__name__, e)
        t = str(e).lower()
        if (
            isinstance(e, TelegramForbiddenError)
            or "peer_id" in t
            or "user_id" in t
            or "user not found" in t
            or "not started" in t
            or "blocked" in t
            or "chat not found" in t
        ):
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[btn("▶️ Start Rumi", url=f"https://t.me/{bot_username}?start=kang")]]
            )
            await _say(
                msg,
                status,
                "🧷 <b>One-time setup</b>\n<blockquote>Tap the button, press Start, then send /kang again.</blockquote>",
                kb,
            )
            return
        if isinstance(e, TelegramRetryAfter):
            ra = int(e.retry_after)
            await _say(
                msg,
                status,
                f"⏳ Telegram says wait <b>{ra}s</b> before adding more stickers.",
            )
            return
        if "too much" in t or "too many" in t or "too_much" in t:
            await _say(
                msg,
                status,
                "⚠️ That pack is full. Telegram allows no more stickers in it.",
            )
            return
        if isinstance(e, TelegramNetworkError):
            await _say(msg, status, "😿 <b>Telegram was slow.</b> Please try again.")
            return
        await _say(msg, status, "😿 <b>Couldn't add it.</b> Telegram refused this sticker.")
        return
    except Exception:
        log.exception("kang crashed")
        await _say(msg, status, "😿 <b>Something slipped.</b> Please try again.")
        return

    link = f"https://t.me/addstickers/{name}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("📦 Open Pack", url=link, style="success")],
            [btn("📤 Share Pack", url=f"https://t.me/share/url?url={link}", style="primary")],
        ]
    )
    await _say(
        msg,
        status,
        f"🧷 <b>STICKER KANGED</b> {emoji}\n\n"
        f"<blockquote>📦 <b>Pack:</b> <code>{html.escape(title)}</code>\n"
        f"🗂 <b>Type:</b> <code>{kind.title()}</code>\n"
        f"🔢 <b>Stickers:</b> <code>{count}</code></blockquote>\n"
        "<i>Tap below to open your pack.</i>",
        kb,
    )


@router.message(Command("kang"))
async def kang_cmd(message: Message, bot: Bot) -> None:
    u = message.from_user
    if u is None:
        return
    src = _source(message)
    if not src:
        src = await _text_source(message, bot)
    if not src:
        await message.reply(HELP, parse_mode=ParseMode.HTML)
        return
    kind, fid, emoji, ext = src
    m = qe.EMOJI_RE.search(" ".join(cmd_args(message)))
    if m:
        emoji = m.group()
    await _run(message, u, bot, kind, fid, emoji, ext, None)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

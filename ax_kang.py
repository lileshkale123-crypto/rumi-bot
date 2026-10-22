"""/kang: copy a sticker or photo into the user's own sticker pack."""
import asyncio
import io
import logging
import time

from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputSticker
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import CommandHandler

import html
import quote_emoji as qe
from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as _K

log = logging.getLogger(__name__)
COOLDOWN = 5
_last = {}
HELP = ("🧷 <b>KANG</b>\n<blockquote>Reply to a sticker or photo with "
        "<code>/kang</code>\nOptional: <code>/kang 😊</code> picks the emoji.</blockquote>")


async def _retry(fn, *a, **kw):
    for i in range(3):
        try:
            return await fn(*a, **kw)
        except BadRequest:
            raise
        except RetryAfter as e:
            ra = e.retry_after
            ra = ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)
            await asyncio.sleep(min(ra + 1, 10))
        except (TimedOut, NetworkError):
            await asyncio.sleep(1 + i)
    return await fn(*a, **kw)


def _source(msg):
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


def _to_png(data):
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = im.size
    k = 512 / max(w, h)
    im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    if out.tell() > 500000:
        out = io.BytesIO()
        im.save(out, "WEBP", quality=85)
    return out.getvalue()


def _gif_to_webm(data):
    import os, shutil, subprocess, tempfile
    d = tempfile.mkdtemp()
    try:
        src = os.path.join(d, "in.bin")
        dst = os.path.join(d, "out.webm")
        with open(src, "wb") as fh:
            fh.write(data)
        vf = "fps=30,scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos,format=yuva420p"
        for crf in ("38", "46", "55"):
            cmd = ["ffmpeg", "-y", "-i", src, "-t", "3", "-an", "-vf", vf,
                   "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", crf, dst]
            subprocess.run(cmd, capture_output=True, timeout=90, check=True)
            with open(dst, "rb") as fh:
                out = fh.read()
            if len(out) <= 256000:
                return out
        raise ValueError("gif too big")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _fit_static(data):
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    ok = im.format in ("PNG", "WEBP")
    if ok and max(w, h) == 512 and w <= 512 and h <= 512 and len(data) <= 500000:
        return data
    return _to_png(data)


async def _text_source(update, context):
    msg = update.effective_message
    r = msg.reply_to_message
    if not r or not (r.text or r.caption):
        return None
    try:
        import asyncio
        import ax_quote as aq
        import quote_pil as qp
        roles = await aq._roles(context.bot, msg.chat_id, [r])
        dicts = await aq._build_msg_dicts(context.bot, [r], roles, include_reply=False)
        img = await asyncio.to_thread(qp.render_messages, dicts, None)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return "static", out.getvalue(), "\U0001F642", "png"
    except Exception:
        log.exception("kang text failed")
        return None


async def kang_cmd(update, context):
    msg, u = update.effective_message, update.effective_user
    src = _source(msg)
    if not src:
        src = await _text_source(update, context)
    if not src:
        return await msg.reply_html(HELP)
    wait = COOLDOWN - (time.monotonic() - _last.get(u.id, -1e9))
    if wait > 0:
        return await msg.reply_html(f"⏳ Wait {int(wait) + 1}s.")
    _last[u.id] = time.monotonic()
    kind, fid, emoji, ext = src
    m = qe.EMOJI_RE.search(" ".join(context.args or []))
    if m:
        emoji = m.group()
    bot = context.bot
    name = f"k{u.id}_{kind}_by_{bot.username}"
    title = f"{u.first_name or 'My'}'s {kind} pack"[:64]
    try:
        if isinstance(fid, (bytes, bytearray)):
            data = bytes(fid)
        else:
            f = await _retry(bot.get_file, fid)
            data = bytes(await _retry(f.download_as_bytearray))
        if ext == "gif":
            data = await asyncio.to_thread(_gif_to_webm, data)
            ext = "webm"
        if kind == "static":
            data = await asyncio.to_thread(_fit_static, data)
            ext = "png"
            if data[:4] == b"RIFF":
                ext = "webp"
        st = InputSticker(InputFile(data, filename=f"s.{ext}", attach=True), [emoji], kind)
        have = True
        try:
            await _retry(bot.get_sticker_set, name)
        except BadRequest as e:
            if "invalid" not in str(e).lower() and "not found" not in str(e).lower():
                raise
            have = False
        if have:
            await _retry(bot.add_sticker_to_set, u.id, name, st, write_timeout=60, read_timeout=60)
        else:
            await _retry(bot.create_new_sticker_set, u.id, name, title, [st], write_timeout=60, read_timeout=60)
    except BadRequest as e:
        _last.pop(u.id, None)
        log.warning("kang failed: %s", e)
        t = str(e).lower()
        if "peer_id" in t or "user_id" in t or "user not found" in t:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start Rumi", url=f"https://t.me/{bot.username}?start=kang")]])
            return await msg.reply_html("🧷 <b>One-time setup</b>\n<blockquote>Tap the button, press Start, then send /kang again.</blockquote>", reply_markup=kb)
            return await msg.reply_html("⚠️ Start me in private chat first, then try again.")
        if "too much" in t or "too many" in t or "too_much" in t:
            return await msg.reply_html("⚠️ That pack is full. Telegram allows no more stickers in it.")
        return await msg.reply_html("😿 <b>Couldn't add it.</b> Telegram refused this sticker.")
    except Exception:
        _last.pop(u.id, None)
        log.exception("kang crashed")
        return await msg.reply_html("😿 <b>Something slipped.</b> Please try again.")
    count = "?"
    try:
        count = len((await bot.get_sticker_set(name)).stickers)
    except Exception:
        pass
    link = f"https://t.me/addstickers/{name}"
    kb = _K([
        [_B("📦 Open Pack", url=link, style="success")],
        [_B("📤 Share Pack", url=f"https://t.me/share/url?url={link}", style="primary")],
    ])
    await msg.reply_html(
        f"🧷 <b>STICKER KANGED</b> {emoji}\n\n"
        f"<blockquote>📦 <b>Pack:</b> <code>{html.escape(title)}</code>\n"
        f"🗂 <b>Type:</b> <code>{kind.title()}</code>\n"
        f"🔢 <b>Stickers:</b> <code>{count}</code></blockquote>\n"
        "<i>Tap below to open your pack.</i>",
        reply_markup=kb,
    )


def register(app):
    app.add_handler(CommandHandler("kang", kang_cmd))

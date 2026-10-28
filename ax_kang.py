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
    im.save(out, "PNG", compress_level=3)
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


_known = {}   # pack name -> sticker count (skips the "does pack exist" call)
_locks = {}   # user id -> Lock, used only while creating a pack


def _set_missing(e):
    t = str(e).lower()
    return "stickerset_invalid" in t or "sticker set not found" in t or "stickerset not found" in t


async def _push_inner(bot, uid, name, title, st):
    """Add a sticker, creating the pack if needed. Returns sticker count."""
    try:
        await _retry(bot.add_sticker_to_set, uid, name, st, write_timeout=60, read_timeout=60)
        if name in _known:
            _known[name] += 1
            return _known[name]
    except BadRequest as e:
        if not _set_missing(e):
            raise
        await _retry(bot.create_new_sticker_set, uid, name, title, [st], write_timeout=60, read_timeout=60)
        _known[name] = 1
        return 1
    try:
        _known[name] = len((await bot.get_sticker_set(name)).stickers)
    except Exception:
        return "?"
    return _known[name]


async def _push(bot, uid, name, title, st):
    # Fast path: no waiting. Lock only until the pack is known (first time),
    # so two parallel kangs can't both try to create the same pack.
    if name in _known:
        return await _push_inner(bot, uid, name, title, st)
    async with _locks.setdefault(uid, asyncio.Lock()):
        return await _push_inner(bot, uid, name, title, st)


async def _download(bot, fid):
    f = await _retry(bot.get_file, fid)
    return bytes(await _retry(f.download_as_bytearray))


async def kang_cmd(update, context):
    msg, u = update.effective_message, update.effective_user
    src = _source(msg)
    if not src:
        src = await _text_source(update, context)
    if not src:
        return await msg.reply_html(HELP)
    kind, fid, emoji, ext = src
    m = qe.EMOJI_RE.search(" ".join(context.args or []))
    if m:
        emoji = m.group()
    bot = context.bot
    await _run(msg, u, bot, kind, fid, emoji, ext, None)


async def _run(msg, u, bot, kind, fid, emoji, ext, status):
    name = f"k{u.id}_{kind}_by_{bot.username}"
    title = f"{u.first_name or 'My'}'s {kind} pack"[:64]
    reply = status.edit_text if status else msg.reply_html
    try:
        count = None
        if isinstance(fid, str) and ext in ("tgs", "webm", "webp") and msg.reply_to_message.sticker:
            # real sticker: reuse Telegram's own file, no download/upload
            try:
                count = await _push(bot, u.id, name, title, InputSticker(fid, [emoji], kind))
            except BadRequest as e:
                t = str(e).lower()
                if "peer_id" in t or "user_id" in t or "user not found" in t or "too much" in t or "too many" in t:
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
            st = InputSticker(InputFile(data, filename=f"s.{ext}", attach=True), [emoji], kind)
            count = await _push(bot, u.id, name, title, st)
    except BadRequest as e:
        log.warning("kang failed: %s", e)
        t = str(e).lower()
        if "peer_id" in t or "user_id" in t or "user not found" in t:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start Rumi", url=f"https://t.me/{bot.username}?start=kang")]])
            return await _say(reply, status, "🧷 <b>One-time setup</b>\n<blockquote>Tap the button, press Start, then send /kang again.</blockquote>", kb)
        if "too much" in t or "too many" in t or "too_much" in t:
            return await _say(reply, status, "⚠️ That pack is full. Telegram allows no more stickers in it.")
        return await _say(reply, status, "😿 <b>Couldn't add it.</b> Telegram refused this sticker.")
    except Exception:
        log.exception("kang crashed")
        return await _say(reply, status, "😿 <b>Something slipped.</b> Please try again.")
    link = f"https://t.me/addstickers/{name}"
    kb = _K([
        [_B("📦 Open Pack", url=link, style="success")],
        [_B("📤 Share Pack", url=f"https://t.me/share/url?url={link}", style="primary")],
    ])
    await _say(reply, status,
        f"🧷 <b>STICKER KANGED</b> {emoji}\n\n"
        f"<blockquote>📦 <b>Pack:</b> <code>{html.escape(title)}</code>\n"
        f"🗂 <b>Type:</b> <code>{kind.title()}</code>\n"
        f"🔢 <b>Stickers:</b> <code>{count}</code></blockquote>\n"
        "<i>Tap below to open your pack.</i>", kb)


async def _say(reply, status, text, kb=None):
    if status:
        return await reply(text, parse_mode="HTML", reply_markup=kb)
    return await reply(text, reply_markup=kb)


def register(app):
    app.add_handler(CommandHandler("kang", kang_cmd))

"""/kang: copy a sticker or photo into the user's own sticker pack."""
import asyncio
import io
import logging
import time

from PIL import Image
from telegram import InputFile, InputSticker
from telegram.error import BadRequest
from telegram.ext import CommandHandler

import quote_emoji as qe

log = logging.getLogger(__name__)
COOLDOWN = 5
_last = {}
HELP = ("🧷 <b>KANG</b>\n<blockquote>Reply to a sticker or photo with "
        "<code>/kang</code>\nOptional: <code>/kang 😊</code> picks the emoji.</blockquote>")


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
    im.save(out, "PNG")
    return out.getvalue()


async def kang_cmd(update, context):
    msg, u = update.effective_message, update.effective_user
    src = _source(msg)
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
        f = await bot.get_file(fid)
        data = bytes(await f.download_as_bytearray())
        if ext == "png":
            data = await asyncio.to_thread(_to_png, data)
        st = InputSticker(InputFile(data, filename=f"s.{ext}"), [emoji], kind)
        try:
            await bot.get_sticker_set(name)
            await bot.add_sticker_to_set(u.id, name, st)
        except BadRequest as e:
            if "invalid" not in str(e).lower() and "not found" not in str(e).lower():
                raise
            await bot.create_new_sticker_set(u.id, name, title, [st])
    except BadRequest as e:
        _last.pop(u.id, None)
        log.warning("kang failed: %s", e)
        t = str(e).lower()
        if "peer_id" in t or "user_id" in t or "user not found" in t:
            return await msg.reply_html("⚠️ Start me in private chat first, then try again.")
        if "too much" in t or "too many" in t:
            return await msg.reply_html("⚠️ That pack is full. Telegram allows no more stickers in it.")
        return await msg.reply_html("😿 <b>Couldn't add it.</b> Telegram refused this sticker.")
    except Exception:
        _last.pop(u.id, None)
        log.exception("kang crashed")
        return await msg.reply_html("😿 <b>Something slipped.</b> Please try again.")
    await msg.reply_html(
        f"🧷 <b>STICKER KANGED</b> {emoji}\n"
        f'<blockquote><a href="https://t.me/addstickers/{name}">Open your {kind} pack</a></blockquote>'
    )


def register(app):
    app.add_handler(CommandHandler("kang", kang_cmd))

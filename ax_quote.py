"""/q quotes one message, /q r also shows the message it replied to."""
import asyncio
import io

from PIL import Image
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from quote_render import render

H = ParseMode.HTML


async def _card(bot, m):
    text = (m.text or m.caption or "").strip()
    if not text:
        return None
    u = m.from_user
    name = (u.first_name if u else None) or getattr(m.sender_chat, "title", None) or "Someone"
    avatar = None
    if u:
        try:
            ph = await bot.get_user_profile_photos(u.id, limit=1)
            if ph.total_count:
                f = await bot.get_file(ph.photos[0][-1].file_id)
                buf = io.BytesIO()
                await f.download_to_memory(out=buf)
                avatar = buf.getvalue()
        except Exception as e:
            print("quote photo failed:", e)
    return await asyncio.to_thread(render, name, text, avatar, u.id if u else 0)


def _stack(imgs):
    crops = [i.crop(i.getbbox()) for i in imgs]
    w = max(c.width for c in crops)
    h = sum(c.height for c in crops) + 8 * (len(crops) - 1)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = 0
    for c in crops:
        canvas.paste(c, (0, y))
        y += c.height + 8
    s = 512 / max(w, h)
    canvas = canvas.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    return canvas


async def quote_cmd(update, context):
    msg = update.effective_message
    rp = msg.reply_to_message
    if not rp:
        await msg.reply_text("💬 <b>Reply to a message</b>\n<blockquote>Use /q on any text message, or /q r to include the message it replied to ✨</blockquote>", parse_mode=H)
        return
    want_two = bool(context.args) and context.args[0].lower() == "r"
    msgs = [rp]
    if want_two and rp.reply_to_message:
        msgs = [rp.reply_to_message, rp]
    try:
        imgs = [c for c in [await _card(context.bot, m) for m in msgs] if c is not None]
        if not imgs:
            await msg.reply_text("🌸 <b>No text found</b>\n<blockquote>I can only quote text messages 🌸</blockquote>", parse_mode=H)
            return
        img = imgs[0] if len(imgs) == 1 else _stack(imgs)
        out = io.BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        await msg.reply_sticker(sticker=out, read_timeout=30, write_timeout=30, connect_timeout=30, pool_timeout=30)
    except Exception as e:
        await msg.reply_text(f"⚠️ <b>Couldn't make the sticker</b>\n<blockquote>{e}</blockquote>", parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("q", quote_cmd))

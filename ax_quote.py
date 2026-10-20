"""/q quotes one message, /q r also shows the message it replied to."""
import asyncio
import io

from PIL import Image
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from quote_render import render
import quote_remote as qr
import html as _html
import quote_cache as qc
import ax_qextra as qx

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


_ROLE_CACHE = {}


async def _roles(bot, chat_id, msgs):
    import time
    out = {}
    now = time.time()
    for m in msgs:
        u = m.from_user
        if u is None or m.chat.type not in ("group", "supergroup"):
            continue
        key = (chat_id, u.id)
        hit = _ROLE_CACHE.get(key)
        if hit and now - hit[0] < 600:
            val = hit[1]
        else:
            val = None
            try:
                cm = await bot.get_chat_member(chat_id, u.id)
                if cm.status == "creator":
                    val = ("owner", getattr(cm, "custom_title", None))
                elif cm.status == "administrator":
                    val = ("admin", getattr(cm, "custom_title", None))
            except Exception as e:
                print("quote role failed:", repr(e)[:100])
            _ROLE_CACHE[key] = (now, val)
        if val:
            out[u.id] = val
    return out


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
    p = qx.parse_args(context.args)
    rp = msg.reply_to_message
    multi = None
    if p["n"] > 1:
        if rp:
            multi = qc.span(msg.chat_id, rp.message_id, p["n"], msg.message_id)
            if len(multi) < p["n"]:
                first = multi[0].message_id if multi else msg.message_id
                multi = qc.last(msg.chat_id, first, p["n"] - len(multi)) + multi
        else:
            multi = qc.last(msg.chat_id, msg.message_id, p["n"])
        if len(multi) < 2:
            await msg.reply_html(
                "🌸 <b>Not enough messages yet, %s</b>\n<blockquote>I only remember "
                "messages sent since my last restart. Try again after a few more.</blockquote>"
                % qx._name(update.effective_user))
            return
        rp = rp or multi[0]
    if not rp:
        await msg.reply_text("💬 <b>Reply to a message</b>\n<blockquote>Use /q on any text message, or /q r to include the message it replied to ✨</blockquote>", parse_mode=H)
        return
    rp = qc.get(update.effective_chat.id, rp.message_id) or rp
    want_two = p["r"] and p["n"] == 1
    if p["bad"]:
        await msg.reply_html(
            "🎨 <b>Didn't know</b> <code>%s</code>\n<blockquote>Try /qcolors "
            "to see colour names.%s</blockquote>"
            % (_html.escape(p["bad"][0]), qx.hint(p["bad"][0])))
    msgs = multi or [rp]
    if want_two and rp.reply_to_message:
        msgs = [rp.reply_to_message, rp]
    try:
        tm = [m for m in msgs if (m.text or m.caption)]
        roles = await _roles(context.bot, msg.chat_id, tm)
        rimg = await qr.remote(context.bot.token, tm, roles, color=p["color"], reply=want_two) if tm else None
        imgs = [rimg] if rimg is not None else [c for c in [await _card(context.bot, m) for m in msgs] if c is not None]
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
    qc.register(app)

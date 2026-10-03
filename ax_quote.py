"""/q quote sticker."""
import asyncio
import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

TERMUX = "/data/data/com.termux/files/usr/share/fonts/TTF/"
BOLD = ["assets/fonts/DejaVuSans-Bold.ttf", TERMUX + "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
REG = ["assets/fonts/DejaVuSans.ttf", TERMUX + "DejaVuSans.ttf",
       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
BUBBLES = [(28, 44, 86), (52, 34, 92), (18, 66, 78), (84, 32, 74), (30, 52, 110)]
NAMES = [(102, 217, 232), (77, 171, 247), (218, 119, 242), (105, 219, 124),
         (255, 121, 180), (255, 212, 59), (150, 160, 255)]


def font(paths, size):
    for p in paths:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def width(d, t, f):
    b = d.textbbox((0, 0), t, font=f)
    return b[2] - b[0]


def wrap(d, text, f, maxw):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            while width(d, w, f) > maxw and len(w) > 1:
                cut = len(w)
                while cut > 1 and width(d, w[:cut], f) > maxw:
                    cut -= 1
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(w[:cut])
                w = w[cut:]
            t = (cur + " " + w).strip()
            if width(d, t, f) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def render(name, text, avatar, uid):
    W, pad, av, px, py, lh = 512, 16, 80, 20, 14, 36
    bx = pad + av + 12
    maxw = W - bx - pad
    nf, bf = font(BOLD, 28), font(REG, 28)
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = wrap(d, text, bf, maxw - 2 * px)
    room = (512 - 2 * pad - 2 * py - 40) // lh
    if len(lines) > room:
        lines = lines[:room]
        lines[-1] = lines[-1].rstrip()[:-1] + "…"
    bh = 2 * py + 40 + lh * len(lines)
    bw = max([width(d, l, bf) for l in lines] + [width(d, name, nf)]) + 2 * px
    bw = min(bw, maxw)
    H = min(512, max(av + 2 * pad, bh + 2 * pad))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    bc = (30, 36, 56, 255)  # steady look
    nc = (102, 217, 232)
    ok = False
    if avatar:
        try:
            a = ImageOps.fit(Image.open(io.BytesIO(avatar)).convert("RGBA"), (av, av))
            m = Image.new("L", (av, av), 0)
            ImageDraw.Draw(m).ellipse([0, 0, av, av], fill=255)
            img.paste(a, (pad, pad), m)
            ok = True
        except Exception:
            pass
    if not ok:
        dr.ellipse([pad, pad, pad + av, pad + av], fill=nc)
        dr.text((pad + av // 2, pad + av // 2), (name[:1] or "?").upper(), font=nf, anchor="mm", fill=(255, 255, 255))
    dr.rounded_rectangle([bx, pad, bx + bw, pad + bh], radius=22, fill=bc)
    dr.polygon([(bx, pad + 24), (bx - 12, pad + 34), (bx, pad + 44)], fill=bc)
    dr.text((bx + px, pad + py - 2), name, font=nf, fill=nc)
    y = pad + py + 40
    for l in lines:
        dr.text((bx + px, y), l, font=bf, fill=(235, 238, 250))
        y += lh
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.paste(img, (0, (512 - H) // 2), img)
    return canvas


async def quote_cmd(update, context):
    msg = update.effective_message
    rp = msg.reply_to_message
    h = ParseMode.HTML
    if not rp:
        await msg.reply_text("💬 <b>Reply to a message</b>\n<blockquote>Use /q on any text message ✨</blockquote>", parse_mode=h)
        return
    text = (rp.text or rp.caption or "").strip()
    if not text:
        await msg.reply_text("💫 <b>No text found</b>\n<blockquote>I can only quote text messages 🌸</blockquote>", parse_mode=h)
        return
    u = rp.from_user
    name = (u.first_name if u else None) or getattr(rp.sender_chat, "title", None) or "Someone"
    uid, avatar = (u.id if u else 0), None
    if u:
        try:
            ph = await context.bot.get_user_profile_photos(u.id, limit=1)
            if ph.total_count:
                f = await context.bot.get_file(ph.photos[0][-1].file_id)
                buf = io.BytesIO()
                await f.download_to_memory(out=buf)
                avatar = buf.getvalue()
        except Exception:
            pass
    try:
        img = await asyncio.to_thread(render, name, text, avatar, uid)
        out = io.BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        await msg.reply_sticker(sticker=out)
    except Exception as e:
        await msg.reply_text(f"⚠️ <b>Couldn't make the sticker</b>\n<blockquote>{e}</blockquote>", parse_mode=h)


def register(app):
    app.add_handler(CommandHandler("q", quote_cmd))


# new drawing: steady size, emoji pictures
from quote_render import render  # noqa: E402,F811

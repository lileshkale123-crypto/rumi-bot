"""/profile: premium image card."""
import asyncio
import html
import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

import config
import db
import services

log = logging.getLogger(__name__)
FD = os.path.join(os.path.dirname(__file__), "assets", "fonts")


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(FD, name), size)


def render(name, level, xp, cash, gems, wins, losses, clan, photo):
    W, H = 900, 480
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(30 + 40 * t), int(16 + 10 * t), int(60 + 50 * t)))
    d.rounded_rectangle((24, 24, W - 24, H - 24), 28, outline=(255, 200, 120), width=3)

    size = 170
    av = Image.new("RGB", (size, size), (90, 70, 150))
    ok = False
    if photo:
        try:
            av = Image.open(io.BytesIO(photo)).convert("RGB").resize((size, size))
            ok = True
        except Exception:
            ok = False
    if not ok:
        ImageDraw.Draw(av).text((size // 2, size // 2), (name[:1] or "R").upper(),
                                font=font(80, True), fill=(255, 255, 255), anchor="mm")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.paste(av, (60, 70), mask)
    d.ellipse((56, 66, 60 + size + 3, 70 + size + 3), outline=(255, 200, 120), width=4)

    d.text((270, 70), name[:18], font=font(40, True), fill=(255, 255, 255))
    d.text((270, 125), f"Level {level}", font=font(26, True), fill=(255, 200, 120))

    base = config.XP_LEVEL_BASE
    lo, hi = (level - 1) ** 2 * base, level ** 2 * base
    frac = min(1.0, max(0.0, (xp - lo) / max(1, hi - lo)))
    d.rounded_rectangle((270, 180, 830, 206), 13, fill=(60, 45, 100))
    if frac > 0:
        d.rounded_rectangle((270, 180, 270 + int(560 * frac), 206), 13, fill=(80, 220, 140))
    d.text((270, 214), f"{xp - lo:,} / {hi - lo:,} XP", font=font(18), fill=(200, 190, 230))

    stats = [("CASH", f"{cash:,}"), ("GEMS", f"{gems:,}"),
             ("ROB W/L", f"{wins}/{losses}"), ("CLAN", clan or "None")]
    for i, (label, val) in enumerate(stats):
        x = 60 + i * 205
        d.rounded_rectangle((x, 300, x + 190, 400), 18, fill=(55, 40, 95))
        d.text((x + 16, 315), label, font=font(16), fill=(170, 160, 210))
        d.text((x + 16, 350), str(val)[:11], font=font(26, True), fill=(255, 255, 255))
    d.text((W // 2, 438), "RUMI", font=font(18, True), fill=(255, 200, 120), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


async def profile_cmd(update, context):
    msg = update.effective_message
    me = update.effective_user
    target = me
    r = msg.reply_to_message
    if r and r.from_user and not r.from_user.is_bot:
        target = r.from_user
    pool = db.get_pool()
    w = await services.get_wallet(pool, target.id)
    if not w:
        await msg.reply_text("No wallet yet 💜 they need to send /start to Rumi first.")
        return
    row = await pool.fetchrow(
        "SELECT c.name FROM clan_members m JOIN clans c ON c.id = m.clan_id "
        "WHERE m.user_id = $1", target.id)
    photo = None
    try:
        ph = await context.bot.get_user_profile_photos(target.id, limit=1)
        if ph.photos:
            f = await context.bot.get_file(ph.photos[0][0].file_id)
            photo = bytes(await f.download_as_bytearray())
    except Exception:
        photo = None
    name = target.first_name or "Player"
    buf = await asyncio.to_thread(
        render, name, services.level_for_xp(w["xp"]), w["xp"], w["cash"],
        w["gems"], w["wins"], w["losses"], row["name"] if row else None, photo)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔴 Close", callback_data=f"prof_close:{me.id}")]])
    await msg.reply_photo(buf, caption=f"<b>{html.escape(name)}</b>'s profile 💜",
                          parse_mode="HTML", reply_markup=kb)


async def close_cb(update, context):
    q = update.callback_query
    owner = int(q.data.split(":")[1])
    if q.from_user.id != owner:
        await q.answer("Only the person who opened this can close it 💜", show_alert=True)
        return
    await q.answer()
    try:
        await q.message.delete()
    except Exception:
        pass


def register(application):
    application.add_handler(CommandHandler("profile", profile_cmd))
    application.add_handler(CallbackQueryHandler(close_cb, pattern=r"^prof_close:\d+$"), group=11)

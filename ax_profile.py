"""/profile: premium image card (aiogram 3.x)."""
import asyncio
import functools
import html
import io
import logging
import os
import unicodedata

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from PIL import Image, ImageDraw, ImageFont

import config
import db
import services

log = logging.getLogger(__name__)
router = Router(name="ax_profile")

FD = os.path.join(os.path.dirname(__file__), "assets", "fonts")


@functools.lru_cache(maxsize=32)
def font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = os.path.join(FD, name)
    if os.path.isfile(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def clean_name(n: str | None) -> str:
    n = unicodedata.normalize("NFKC", n or "")
    keep = [c for c in n if ord(c) < 0x250 and unicodedata.category(c)[0] in "LNPZ"]
    out = " ".join("".join(keep).split())
    return out or "Player"


def render(
    name: str,
    level: int,
    xp: int,
    cash: int,
    gems: int,
    wins: int,
    losses: int,
    clan: str | None,
    photo: bytes | None,
    title: str | None = None,
) -> bytes:
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
            av = Image.open(io.BytesIO(photo)).convert("RGB").resize((size, size), Image.LANCZOS)
            ok = True
        except Exception:
            ok = False
    if not ok:
        ImageDraw.Draw(av).text(
            (size // 2, size // 2),
            (name[:1] or "R").upper(),
            font=font(80, True),
            fill=(255, 255, 255),
            anchor="mm",
        )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.paste(av, (60, 70), mask)
    d.ellipse((56, 66, 60 + size + 3, 70 + size + 3), outline=(255, 200, 120), width=4)

    d.text((270, 70), name[:18], font=font(40, True), fill=(255, 255, 255))
    d.text((270, 125), f"Level {level}", font=font(26, True), fill=(255, 200, 120))
    if title:
        d.text((450, 130), title, font=font(22, True), fill=(255, 230, 160))

    base = config.XP_LEVEL_BASE
    lo, hi = (level - 1) ** 2 * base, level ** 2 * base
    frac = min(1.0, max(0.0, (xp - lo) / max(1, hi - lo)))
    d.rounded_rectangle((270, 180, 830, 206), 13, fill=(60, 45, 100))
    if frac > 0:
        d.rounded_rectangle((270, 180, 270 + int(560 * frac), 206), 13, fill=(80, 220, 140))
    d.text((270, 214), f"{xp - lo:,} / {hi - lo:,} XP", font=font(18), fill=(200, 190, 230))

    stats = [
        ("CASH", f"{cash:,}"),
        ("GEMS", f"{gems:,}"),
        ("ROB W/L", f"{wins}/{losses}"),
        ("CLAN", clan or "None"),
    ]
    for i, (label, val) in enumerate(stats):
        x = 60 + i * 205
        d.rounded_rectangle((x, 300, x + 190, 400), 18, fill=(55, 40, 95))
        d.text((x + 16, 315), label, font=font(16), fill=(170, 160, 210))
        d.text((x + 16, 350), str(val)[:11], font=font(26, True), fill=(255, 255, 255))
    d.text((W // 2, 438), "RUMI", font=font(18, True), fill=(255, 200, 120), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def _fetch_avatar(bot: Bot, user_id: int) -> bytes | None:
    try:
        ph = await bot.get_user_profile_photos(user_id, limit=1)
        if ph.photos:
            f = await bot.get_file(ph.photos[0][0].file_id)
            if f.file_path:
                buf = io.BytesIO()
                await bot.download_file(f.file_path, destination=buf)
                return buf.getvalue()
    except Exception:
        pass
    return None


@router.message(Command("profile"))
async def profile_cmd(message: Message, bot: Bot, pool: asyncpg.Pool | None = None) -> None:
    me = message.from_user
    if me is None:
        return
    target = me
    r = message.reply_to_message
    if r and r.from_user and not r.from_user.is_bot:
        target = r.from_user

    p = pool or db.get_pool()
    w = await services.get_wallet(p, target.id)
    if not w:
        await message.reply("No wallet yet 💜 they need to send /start to Rumi first.")
        return

    import ax_shop

    clan_row, photo, card_title = await asyncio.gather(
        p.fetchrow(
            "SELECT c.name FROM clan_members m JOIN clans c ON c.id = m.clan_id "
            "WHERE m.user_id = $1",
            target.id,
        ),
        _fetch_avatar(bot, target.id),
        ax_shop.get_title(p, target.id),
        return_exceptions=True,
    )
    clan_name = clan_row["name"] if (clan_row and not isinstance(clan_row, BaseException)) else None
    photo_bytes = photo if isinstance(photo, (bytes, bytearray)) else None
    title_str = clean_name(card_title) if isinstance(card_title, str) and card_title else None

    name = target.first_name or "Player"
    card_name = clean_name(name)
    png_bytes = await asyncio.to_thread(
        render,
        card_name,
        services.level_for_xp(w["xp"]),
        w["xp"],
        w["cash"],
        w["gems"],
        w["wins"],
        w["losses"],
        clan_name,
        photo_bytes,
        title_str,
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔴 Close", callback_data=f"prof_close:{me.id}")]]
    )
    await message.reply_photo(
        photo=BufferedInputFile(png_bytes, filename="profile.png"),
        caption=f"<b>{html.escape(name)}</b>'s profile 💜",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


@router.callback_query(F.data.regexp(r"^prof_close:\d+$"))
async def close_cb(query: CallbackQuery) -> None:
    owner = int((query.data or "0:0").split(":")[1])
    if query.from_user.id != owner:
        await query.answer("Only the person who opened this can close it 💜", show_alert=True)
        return
    await query.answer()
    if query.message:
        try:
            await query.message.delete()
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

"""Extras for /q: colour names, argument parser, /qcolors and /mypacks (aiogram 3.x)."""
import asyncio
import difflib
import html
import logging
import re

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, Message

from ax_common import btn, get_bot_username

log = logging.getLogger(__name__)
router = Router(name="ax_qextra")

MAX_N = 5
COLORS = {
    "red": "#a8232b", "blue": "#16419b", "pink": "#ffc1dc",
    "green": "#16663a", "purple": "#7b3fe4", "orange": "#ffcf9e",
    "yellow": "#fff0a0", "teal": "#0b6f6d", "grey": "#4b5563",
    "gray": "#4b5563", "black": "#000000", "white": "#ffffff",
}
SHOW = (
    ("red", "🔴"), ("blue", "🔵"), ("pink", "🌸"), ("green", "🟢"),
    ("purple", "🟣"), ("orange", "🟠"), ("yellow", "🟡"), ("teal", "🩵"),
    ("grey", "🩶"), ("black", "⚫"), ("white", "⚪"),
)
KINDS = (
    ("static", "🖼", "Static"),
    ("animated", "✨", "Animated"),
    ("video", "🎬", "Video"),
)
HEX_RE = re.compile(r"^#([0-9a-f]{6}|[0-9a-f]{3})$")


def parse_args(args: list[str] | None) -> dict:
    """Read /q words in any order: r, a number, a colour name or #hex."""
    out = {"r": False, "n": 1, "color": None, "bad": []}
    for a in args or []:
        t = a.strip().lower()
        if not t:
            continue
        if t == "r":
            out["r"] = True
        elif t.isascii() and t.isdigit():
            out["n"] = max(1, min(int(t), MAX_N))
        elif t in COLORS:
            out["color"] = COLORS[t]
        else:
            m = HEX_RE.match(t)
            if not m:
                out["bad"].append(a[:20])
                continue
            h = m.group(1)
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            out["color"] = "#" + h
    return out


def hint(word: str) -> str:
    m = difflib.get_close_matches(word.lower(), list(COLORS), n=1, cutoff=0.6)
    return " Did you mean <code>%s</code>?" % m[0] if m else ""


def _name(u) -> str:
    return html.escape((u.first_name if u else None) or "friend")


@router.message(Command("qcolors"))
async def qcolors_cmd(message: Message) -> None:
    names = " · ".join("%s %s" % (e, n) for n, e in SHOW)
    await message.reply(
        "🎨 <b>Quote colours, %s</b>\n"
        "<blockquote>%s</blockquote>\n"
        "<b>Try it</b> (reply to a message):\n"
        "<code>/q red</code>\n<code>/q r blue</code>\n"
        "<code>/q 3 pink</code>\n<code>/q #ff66aa</code>\n"
        "<i>Any #hex code works too. Max %d messages for /q 3.</i>"
        % (_name(message.from_user), names, MAX_N),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("mypacks"))
async def mypacks_cmd(message: Message, bot: Bot) -> None:
    u = message.from_user
    if u is None:
        return
    uname = await get_bot_username(bot)
    pnames = [f"k{u.id}_{kind}_by_{uname}" for kind, _, _ in KINDS]
    results = await asyncio.gather(
        *(bot.get_sticker_set(p) for p in pnames),
        return_exceptions=True,
    )

    found, failed = [], False
    for (kind, emo, label), pname, res in zip(KINDS, pnames, results):
        if isinstance(res, TelegramBadRequest):
            continue
        if isinstance(res, BaseException):
            failed = True
            log.warning("mypacks %s: %s", kind, res)
            continue
        found.append((emo, label, pname, len(res.stickers)))

    if not found:
        if failed:
            await message.reply(
                "🌧 <b>Couldn't check your packs.</b> Please try again.",
                parse_mode=ParseMode.HTML,
            )
            return
        await message.reply(
            "📦 <b>No packs yet, %s</b>\n<blockquote>Reply to any sticker or "
            "photo with <code>/kang</code> and I'll start your pack.</blockquote>" % _name(u),
            parse_mode=ParseMode.HTML,
        )
        return

    lines, rows = [], []
    for i, (emo, label, pname, n) in enumerate(found):
        lines.append(
            "%s <b>%s</b> · <code>%d</code> sticker%s"
            % (emo, label, n, "" if n == 1 else "s")
        )
        rows.append(
            [
                btn(
                    "%s Open %s pack" % (emo, label.lower()),
                    url="https://t.me/addstickers/" + pname,
                    style="success" if i % 2 == 0 else "primary",
                )
            ]
        )
    note = "\n⚠️ <i>Couldn't check some packs.</i>" if failed else ""
    await message.reply(
        "🎀 <b>%s's packs</b>\n<blockquote>%s</blockquote>\n"
        "<i>Each type has its own pack, that's a Telegram rule.</i>%s"
        % (_name(u), "\n".join(lines), note),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

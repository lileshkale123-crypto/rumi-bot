"""Meter and couple commands: love crush couples look brain stupid_meter (aiogram 3.x)."""
import hashlib
import random
from datetime import datetime

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import ax_love
from ax_common import esc

router = Router(name="ax_meter")
H = ParseMode.HTML

KINDS = {
    "love": ("💞", "Love Meter", "ship"),
    "crush": ("🌹", "Crush Meter", None),
    "look": ("✨", "Look Meter", None),
    "brain": ("🧠", "Brain Meter", None),
    "stupid_meter": ("🤪", "Stupid Meter", None),
}
TIERS = {
    "love": [
        "Just friendly sparks", "A tiny spark is glowing", "Something sweet is growing",
        "A lovely bond is forming", "Hearts beating in rhythm", "A love written in the stars",
    ],
    "crush": [
        "Not on the radar yet", "A faint little glance", "A secret smile appears",
        "The heart is fluttering", "A big crush for sure", "Completely head over heels",
    ],
    "look": [
        "A hidden gem in disguise", "Cute in a quiet way", "Looking sharp today",
        "A real head turner", "Stunning from every angle", "Pure royalty, a living masterpiece",
    ],
    "brain": [
        "Still loading, be patient", "A curious little mind", "Pretty bright thinking",
        "A sharp and clever head", "A brilliant mind at work", "Genius level, bow down",
    ],
    "stupid_meter": [
        "Wise as an owl", "Mostly sensible", "A little silly sometimes",
        "Cute chaos energy", "Royally goofy today", "Legendary silliness, a national treasure",
    ],
}

_couples_day: dict[int, tuple[str, tuple[int, str], tuple[int, str]]] = {}
_COUPLES_MAX = 5000


def link(uid: int, name: str | None) -> str:
    return '<b><a href="tg://user?id=%d">%s</a></b>' % (uid, esc(name or "Someone"))


def today() -> str:
    return datetime.now(ax_love.IST).strftime("%Y-%m-%d")


def pct(kind: str, a: int, b: int) -> int:
    if kind == "love":
        return ax_love.score(a, b, today())
    if kind == "crush":
        key = "%s-%s>%s-%s" % (kind, a, b, today())
    else:
        key = "%s-%s-%s" % (kind, b, today())
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 101


def tier(kind: str, p: int) -> str:
    i = 0 if p <= 20 else 1 if p <= 40 else 2 if p <= 60 else 3 if p <= 80 else 4 if p <= 95 else 5
    return TIERS[kind][i]


def card(kind: str, a: str, b: str, p: int) -> str:
    icon, title, _ = KINDS[kind]
    f = max(0, min(10, round(p / 10)))
    mid = "💞" if kind == "love" else "➜"
    return (
        "%s <b>%s</b>\n\n<blockquote>👤 %s\n%s %s</blockquote>\n"
        "%s%s <b>%d%%</b>\n<i>%s</i>"
        % (icon, title, a, mid, b, "▰" * f, "▱" * (10 - f), p, tier(kind, p))
    )


def make(kind: str):
    async def handler(message: Message) -> None:
        u = message.from_user
        if u is None:
            return
        r = message.reply_to_message
        if not r or not r.from_user:
            await message.reply(
                "%s <b>Reply to someone</b> with /%s" % (KINDS[kind][0], kind),
                parse_mode=H,
            )
            return
        t = r.from_user
        txt = card(kind, link(u.id, u.first_name), link(t.id, t.first_name), pct(kind, u.id, t.id))
        img = KINDS[kind][2]
        if img:
            await ax_love.send(message, img, txt)
        else:
            await message.reply(txt, parse_mode=H)

    return handler


@router.message(Command("couples"))
async def couples_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if await ax_love.groups_only(message):
        return
    chat_id = message.chat.id
    day = today()
    pair = _couples_day.get(chat_id)
    if not pair or pair[0] != day:
        rows = sorted(await ax_love.members(pool, chat_id, bot.id), key=lambda r: r["id"])
        if len(rows) < 2:
            await message.reply(
                "💫 <b>Not enough members yet</b>\n<blockquote>Let more people chat first ✨</blockquote>",
                parse_mode=H,
            )
            return
        a, b = random.Random("%s-%s" % (chat_id, day)).sample(rows, 2)
        pair = (day, (a["id"], a["first_name"] or "Someone"), (b["id"], b["first_name"] or "Someone"))
        if len(_couples_day) >= _COUPLES_MAX:
            stale = [cid for cid, v in _couples_day.items() if v[0] != day]
            for cid in stale:
                _couples_day.pop(cid, None)
            if len(_couples_day) >= _COUPLES_MAX:
                _couples_day.pop(next(iter(_couples_day)), None)
        _couples_day[chat_id] = pair

    _, a, b = pair
    p = ax_love.score(a[0], b[0], day)
    f = max(0, min(10, round(p / 10)))
    txt = (
        "💘 <b>Couple of the Day</b>\n\n<blockquote>👤 %s\n💞 %s</blockquote>\n"
        "%s%s <b>%d%%</b>\n<i>%s</i>\n\n<i>A new couple arrives tomorrow ✨</i>"
        % (link(a[0], a[1]), link(b[0], b[1]), "▰" * f, "▱" * (10 - f), p, tier("love", p))
    )
    await ax_love.send(message, "crush", txt)


for _k in KINDS:
    router.message(Command(_k))(make(_k))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

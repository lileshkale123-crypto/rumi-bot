"""Meter and couple commands: love crush couples look brain stupid_meter."""
import hashlib
import random
from datetime import datetime
from telegram.constants import ParseMode
from telegram.ext import CommandHandler
from ax_common import esc
import ax_love

H = ParseMode.HTML
KINDS = {
    "love": ("💞", "Love Meter", "ship"),
    "crush": ("🌹", "Crush Meter", "crush"),
    "look": ("✨", "Look Meter", None),
    "brain": ("🧠", "Brain Meter", None),
    "stupid_meter": ("🤪", "Stupid Meter", None),
}
TIERS = {
    "love": ["Just friendly sparks", "A tiny spark is glowing", "Something sweet is growing",
             "A lovely bond is forming", "Hearts beating in rhythm", "A love written in the stars"],
    "crush": ["Not on the radar yet", "A faint little glance", "A secret smile appears",
              "The heart is fluttering", "A big crush for sure", "Completely head over heels"],
    "look": ["A hidden gem in disguise", "Cute in a quiet way", "Looking sharp today",
             "A real head turner", "Stunning from every angle", "Pure royalty, a living masterpiece"],
    "brain": ["Still loading, be patient", "A curious little mind", "Pretty bright thinking",
              "A sharp and clever head", "A brilliant mind at work", "Genius level, bow down"],
    "stupid_meter": ["Wise as an owl", "Mostly sensible", "A little silly sometimes",
                     "Cute chaos energy", "Royally goofy today", "Legendary silliness, a national treasure"],
}


def link(uid, name):
    return '<b><a href="tg://user?id=%d">%s</a></b>' % (uid, esc(name or "Someone"))


def today():
    return datetime.now(ax_love.IST).strftime("%Y-%m-%d")


def pct(kind, a, b):
    if kind == "love":
        return ax_love.score(a, b, today())
    key = "%s-%s-%s" % (kind, a, today()) if kind == "crush" else "%s-%s-%s" % (kind, b, today())
    if kind == "crush":
        key = "%s-%s>%s-%s" % (kind, a, b, today())
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 101


def tier(kind, p):
    i = 0 if p <= 20 else 1 if p <= 40 else 2 if p <= 60 else 3 if p <= 80 else 4 if p <= 95 else 5
    return TIERS[kind][i]


def card(kind, a, b, p):
    icon, title, _ = KINDS[kind]
    f = max(0, min(10, round(p / 10)))
    mid = "💞" if kind == "love" else "➜"
    return ("%s <b>%s</b>\n\n<blockquote>👤 %s\n%s %s</blockquote>\n"
            "%s%s <b>%d%%</b>\n<i>%s</i>"
            % (icon, title, a, mid, b, "▰" * f, "▱" * (10 - f), p, tier(kind, p)))


def make(kind):
    async def handler(update, context):
        msg = update.effective_message
        u = update.effective_user
        if not msg or not u:
            return
        r = msg.reply_to_message
        if not r or not r.from_user:
            await msg.reply_text("%s <b>Reply to someone</b> with /%s" % (KINDS[kind][0], kind), parse_mode=H)
            return
        t = r.from_user
        txt = card(kind, link(u.id, u.first_name), link(t.id, t.first_name), pct(kind, u.id, t.id))
        img = KINDS[kind][2]
        if img:
            await ax_love.send(update, img, txt)
        else:
            await msg.reply_text(txt, parse_mode=H)
    return handler


async def couples_cmd(update, context):
    if await ax_love.groups_only(update):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    rows = list(await ax_love.members(pool, chat_id, context.bot.id))
    if len(rows) < 2:
        await update.effective_message.reply_text(
            "💫 <b>Not enough members yet</b>\n<blockquote>Let more people chat first ✨</blockquote>", parse_mode=H)
        return
    random.shuffle(rows)
    out = []
    for i in range(min(5, len(rows) // 2)):
        a, b = rows[2 * i], rows[2 * i + 1]
        p = ax_love.score(a["id"], b["id"], today())
        out.append("💞 %s  ×  %s  <b>%d%%</b>" % (link(a["id"], a["first_name"]), link(b["id"], b["first_name"]), p))
    await update.effective_message.reply_text(
        "💘 <b>Today's Random Couples</b>\n<blockquote>%s</blockquote>\n<i>Cupid has spoken ✨</i>" % "\n".join(out),
        parse_mode=H)


def register(app):
    for k in KINDS:
        app.add_handler(CommandHandler(k, make(k)))
    app.add_handler(CommandHandler("couples", couples_cmd))

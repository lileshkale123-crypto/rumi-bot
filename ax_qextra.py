"""Extras for /q: colour names, argument parser, /qcolors and /mypacks."""
import difflib
import html
import logging
import re

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as K
from telegram.error import BadRequest
from telegram.ext import CommandHandler

log = logging.getLogger(__name__)
MAX_N = 5
COLORS = {
    "red": "#a8232b", "blue": "#16419b", "pink": "#ffc1dc",
    "green": "#16663a", "purple": "#7b3fe4", "orange": "#ffcf9e",
    "yellow": "#fff0a0", "teal": "#0b6f6d", "grey": "#4b5563",
    "gray": "#4b5563", "black": "#000000", "white": "#ffffff",
}
SHOW = (("red", "🔴"), ("blue", "🔵"), ("pink", "🌸"), ("green", "🟢"),
        ("purple", "🟣"), ("orange", "🟠"), ("yellow", "🟡"), ("teal", "🩵"),
        ("grey", "🩶"), ("black", "⚫"), ("white", "⚪"))
KINDS = (("static", "🖼", "Static"), ("animated", "✨", "Animated"),
         ("video", "🎬", "Video"))
HEX_RE = re.compile(r"^#([0-9a-f]{6}|[0-9a-f]{3})$")


def parse_args(args):
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


def hint(word):
    m = difflib.get_close_matches(word.lower(), list(COLORS), n=1, cutoff=0.6)
    return " Did you mean <code>%s</code>?" % m[0] if m else ""


def _name(u):
    return html.escape((u.first_name if u else None) or "friend")


async def qcolors_cmd(update, context):
    msg = update.effective_message
    names = " · ".join("%s %s" % (e, n) for n, e in SHOW)
    await msg.reply_html(
        "🎨 <b>Quote colours, %s</b>\n"
        "<blockquote>%s</blockquote>\n"
        "<b>Try it</b> (reply to a message):\n"
        "<code>/q red</code>\n<code>/q r blue</code>\n"
        "<code>/q 3 pink</code>\n<code>/q #ff66aa</code>\n"
        "<i>Any #hex code works too. Max %d messages for /q 3.</i>"
        % (_name(update.effective_user), names, MAX_N))


async def mypacks_cmd(update, context):
    msg, u, bot = update.effective_message, update.effective_user, context.bot
    found, failed = [], False
    for kind, emo, label in KINDS:
        pname = "k%s_%s_by_%s" % (u.id, kind, bot.username)
        try:
            st = await bot.get_sticker_set(pname)
            found.append((emo, label, pname, len(st.stickers)))
        except BadRequest:
            pass
        except Exception as e:
            failed = True
            log.warning("mypacks %s: %s", kind, e)
    if not found:
        if failed:
            return await msg.reply_html(
                "🌧 <b>Couldn't check your packs.</b> Please try again.")
        return await msg.reply_html(
            "📦 <b>No packs yet, %s</b>\n<blockquote>Reply to any sticker or "
            "photo with <code>/kang</code> and I'll start your pack.</blockquote>"
            % _name(u))
    lines, rows = [], []
    for i, (emo, label, pname, n) in enumerate(found):
        lines.append("%s <b>%s</b> · <code>%d</code> sticker%s"
                     % (emo, label, n, "" if n == 1 else "s"))
        rows.append([B("%s Open %s pack" % (emo, label.lower()),
                       url="https://t.me/addstickers/" + pname,
                       style="success" if i % 2 == 0 else "primary")])
    note = "\n⚠️ <i>Couldn't check some packs.</i>" if failed else ""
    await msg.reply_html(
        "🎀 <b>%s's packs</b>\n<blockquote>%s</blockquote>\n"
        "<i>Each type has its own pack, that's a Telegram rule.</i>%s"
        % (_name(u), "\n".join(lines), note),
        reply_markup=K(rows))


def register(app):
    app.add_handler(CommandHandler("mypacks", mypacks_cmd))
    app.add_handler(CommandHandler("qcolors", qcolors_cmd))


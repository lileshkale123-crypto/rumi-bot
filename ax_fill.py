"""Rose-style fillings, random replies and buttons."""
import random
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ax_common import esc

BTN = re.compile(
    r"\[([^\]]+)\]\(buttonurl://([^)\s]+?)((?::(?:same|blue|green|red))*)\)")
STYLES = {"blue": "primary", "green": "success", "red": "danger"}
FLAGS = ("{preview}", "{nonotif}", "{protect}", "{mediaspoiler}")


def pick(text):
    """Choose one random reply when text uses %%% separators."""
    parts = [p.strip() for p in (text or "").split("%%%") if p.strip()]
    return random.choice(parts) if parts else ""


def parse(text):
    """Split raw text into (body, rows, flags). Buttons marked :same share a row."""
    rows = []
    for m in BTN.finditer(text):
        label, url, opts = m.group(1), m.group(2), m.group(3) or ''
        words = [w for w in opts.split(':') if w]
        style = None
        for w in words:
            if w in STYLES:
                style = STYLES[w]
        item = (label, url, style)
        if 'same' in words and rows:
            rows[-1].append(item)
        else:
            rows.append([item])
    body = BTN.sub("", text)
    flags = {f.strip("{}"): (f in body) for f in FLAGS}
    for f in FLAGS:
        body = body.replace(f, "")
    return body.strip(), rows, flags


def fill(body, user, chat, rules_link=None):
    """Replace fillings. Old {first_name} and {chat_name} keep working."""
    first = esc(user.first_name or "")
    last = esc(user.last_name or "")
    full = (first + " " + last).strip()
    uname = "@" + user.username if user.username else first
    chat_name = esc(chat.title or "this group")
    mention = '<a href="tg://user?id=%d">%s</a>' % (user.id, first or "friend")
    table = {
        "{first}": first, "{first_name}": first, "{last}": last,
        "{fullname}": full, "{username}": esc(uname), "{mention}": mention,
        "{id}": str(user.id), "{chatname}": chat_name,
        "{chat_name}": chat_name,
    }
    for k, v in table.items():
        body = body.replace(k, v)
    if rules_link:
        body = body.replace("{rules}", "").replace("{rules:same}", "")
    return body


def has_rules(text):
    return "{rules}" in text or "{rules:same}" in text


def keyboard(rows, rules_url=None, rules_same=False, note_url=None):
    """Build an InlineKeyboardMarkup from parsed rows."""
    out = []
    for r in rows:
        line = []
        for label, url, style in r:
            if url.startswith("#") and note_url:
                url = note_url(url[1:])
            kw = {"api_kwargs": {"style": style}} if style else {}
            line.append(InlineKeyboardButton(label, url=url, **kw))
        out.append(line)
    if rules_url:
        b = InlineKeyboardButton("📜 Rules", url=rules_url)
        if rules_same and out:
            out[-1].append(b)
        else:
            out.append([b])
    return InlineKeyboardMarkup(out) if out else None

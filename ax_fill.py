"""Rose-style fillings, random replies and buttons (aiogram 3.x)."""
import random
import re
from typing import Callable

from aiogram.types import InlineKeyboardMarkup

from ax_common import btn, esc

BTN = re.compile(
    r"\[([^\]]+)\]\((?:buttonurl://)?((?:https?://|t\.me/|tg://|#)[^)\s:]+)((?::(?:same|blue|green|red))*)\)"
)
STYLES = {"blue": "primary", "green": "success", "red": "danger"}
FLAGS = ("{preview}", "{nonotif}", "{protect}", "{mediaspoiler}")


def pick(text: str | None) -> str:
    """Choose one random reply when text uses %%% separators."""
    parts = [p.strip() for p in (text or "").split("%%%") if p.strip()]
    return random.choice(parts) if parts else ""


def parse(text: str | None) -> tuple[str, list[list[tuple[str, str, str | None]]], dict[str, bool]]:
    """Split raw text into (body, rows, flags). Buttons marked :same share a row."""
    rows: list[list[tuple[str, str, str | None]]] = []
    for m in BTN.finditer(text or ""):
        label, url, opts = m.group(1), m.group(2), m.group(3) or ""
        if url.startswith("t.me/"):
            url = "https://" + url
        words = [w for w in opts.split(":") if w]
        style = None
        for w in words:
            if w in STYLES:
                style = STYLES[w]
        item = (label, url, style)
        if "same" in words and rows:
            rows[-1].append(item)
        else:
            rows.append([item])
    body = BTN.sub("", text or "")
    flags = {f.strip("{}"): (f in body) for f in FLAGS}
    for f in FLAGS:
        body = body.replace(f, "")
    return body.strip(), rows, flags


def fill(body: str, user, chat, rules_link: str | None = None) -> str:
    """Replace fillings. Old {first_name} and {chat_name} keep working."""
    first = esc(getattr(user, "first_name", None) or "")
    last = esc(getattr(user, "last_name", None) or "")
    full = (first + " " + last).strip()
    username = getattr(user, "username", None)
    uname = "@" + username if username else first
    chat_name = esc(getattr(chat, "title", None) or "this group")
    uid = getattr(user, "id", 0)
    mention = '<a href="tg://user?id=%d">%s</a>' % (uid, first or "friend")
    table = {
        "{first}": first,
        "{first_name}": first,
        "{last}": last,
        "{fullname}": full,
        "{username}": esc(uname),
        "{mention}": mention,
        "{id}": str(uid),
        "{chatname}": chat_name,
        "{chat_name}": chat_name,
    }
    for k, v in table.items():
        body = body.replace(k, v)
    if rules_link or has_rules(body):
        body = body.replace("{rules}", "").replace("{rules:same}", "").strip()
    return body


def has_rules(text: str | None) -> bool:
    return "{rules}" in (text or "") or "{rules:same}" in (text or "")


def keyboard(
    rows: list[list[tuple[str, str, str | None]]],
    rules_url: str | None = None,
    rules_same: bool = False,
    note_url: Callable[[str], str] | None = None,
    rules_label: str | None = None,
) -> InlineKeyboardMarkup | None:
    """Build an InlineKeyboardMarkup from parsed rows."""
    out = []
    for r in rows:
        line = []
        for label, url, style in r:
            if url.startswith("#"):
                if not note_url:
                    continue
                url = note_url(url[1:])
            if not url.startswith(("http://", "https://", "tg://")):
                continue
            line.append(btn(label, url=url, style=style))
        if line:
            out.append(line)
    if rules_url:
        b = btn(rules_label or "📜 Rules", url=rules_url)
        if rules_same and out:
            out[-1].append(b)
        else:
            out.append([b])
    return InlineKeyboardMarkup(inline_keyboard=out) if out else None

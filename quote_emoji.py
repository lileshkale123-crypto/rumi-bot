"""Helpers for /q: fancy letters -> plain, emoji -> Twemoji pictures."""
import io
import os
import re
import unicodedata

import httpx
from PIL import Image

CACHE_DIR = "emoji_cache"
URL = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{}.png"
_mem = {}

SMALL_CAPS = dict(zip("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ", "abcdefghijklmnopqrstuvwxyz"))

_MOD = r"[\uFE0F\U0001F3FB-\U0001F3FF]?"
_BASE = (r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF"
         r"\u203C\u2049\u2122\u2139\u24C2\u25AA-\u25FE\u2934\u2935"
         r"\u3030\u303D\u3297\u3299\u00A9\u00AE]")
EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[0-9#*]\uFE0F?\u20E3"
    r"|" + _BASE + _MOD + r"(?:\u200D" + _BASE + _MOD + r")*"
)


def clean(text):
    """Fancy letters the font cannot draw -> plain letters."""
    out = []
    for c in text:
        o = ord(c)
        if c in SMALL_CAPS:
            out.append(SMALL_CAPS[c])
        elif 0x1D400 <= o <= 0x1D7FF or 0xFF01 <= o <= 0xFF5E or 0x24B6 <= o <= 0x24E9:
            out.append(unicodedata.normalize("NFKC", c))
        else:
            out.append(c)
    return "".join(out)


def split(text):
    """Returns a list of ('t', text) and ('e', emoji) pieces."""
    parts, pos = [], 0
    for m in EMOJI_RE.finditer(text):
        if m.start() > pos:
            parts.append(("t", text[pos:m.start()]))
        parts.append(("e", m.group()))
        pos = m.end()
    if pos < len(text):
        parts.append(("t", text[pos:]))
    return parts


def _name(e):
    if "\u200d" not in e:
        e = e.replace("\ufe0f", "")
    return "-".join(f"{ord(c):x}" for c in e)


def fetch(e):
    """Emoji picture (RGBA, 72x72) or None. Downloaded once, then cached."""
    key = _name(e)
    if key in _mem:
        return _mem[key]
    path = os.path.join(CACHE_DIR, key + ".png")
    try:
        if os.path.isfile(path):
            data = open(path, "rb").read()
        else:
            r = httpx.get(URL.format(key), timeout=6)
            if r.status_code != 200:
                raise ValueError(r.status_code)
            data = r.content
            os.makedirs(CACHE_DIR, exist_ok=True)
            open(path, "wb").write(data)
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        img = None
    _mem[key] = img
    return img

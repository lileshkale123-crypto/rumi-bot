"""Helpers for /q: fancy letters -> plain, emoji -> Twemoji/Google pictures."""
import io
import os
import re
import unicodedata
import httpx
from PIL import Image
import quote_fb as qf

CACHE_DIR = "emoji_cache_g"
URL = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{}.png"
GURL = "https://cdn.jsdelivr.net/npm/emoji-datasource-google@15.0.1/img/google/64/{}.png"
_mem = {}

SMALL_CAPS = dict(zip("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ", "abcdefghijklmnopqrstuvwxyz"))

# Comprehensive Unicode Emoji regex covering Emoji 15.0, ZWJ sequences,
# skin tones, flags, keycaps, dingbats, geometric shapes & common symbols.
_MOD = r"(?:\uFE0F|\U0001F3FB|\U0001F3FC|\U0001F3FD|\U0001F3FE|\U0001F3FF)*"
_BASE = (
    r"(?:[\U0001F1E6-\U0001F1FF]{2}"
    r"|[0-9#*]\uFE0F?\u20E3"
    r"|[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF"
    r"\u203C\u2049\u2122\u2139\u2194-\u21AA\u24C2\u25AA-\u25FE\u2934\u2935"
    r"\u3030\u303D\u3297\u3299\u00A9\u00AE])"
)
EMOJI_RE = re.compile(_BASE + _MOD + r"(?:\u200D" + _BASE + _MOD + r")*")


def clean(text):
    """Fancy mathematical/fullwidth letters the font cannot draw -> plain letters."""
    out = []
    for c in text:
        o = ord(c)
        if c in SMALL_CAPS:
            out.append(SMALL_CAPS[c])
        elif 0x1D400 <= o <= 0x1D7FF or 0xFF01 <= o <= 0xFF5E or 0x24B6 <= o <= 0x24E9:
            out.append(c if qf.covered(c) else unicodedata.normalize("NFKC", c))
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


def _candidate_keys(e):
    """Generate candidate hex codepoint keys (with and without FE0F) for CDN lookup."""
    full_hex = "-".join(f"{ord(c):x}" for c in e)
    no_fe0f = "-".join(f"{ord(c):x}" for c in e if ord(c) != 0xFE0F)
    keys = []
    for k in (no_fe0f, full_hex):
        if k and k not in keys:
            keys.append(k)
    return keys


def fetch(e):
    """Emoji picture (RGBA) or None. Downloaded once, then cached on disk & memory."""
    keys = _candidate_keys(e)
    if not keys:
        return None
    primary = keys[0]
    if primary in _mem:
        return _mem[primary]

    os.makedirs(CACHE_DIR, exist_ok=True)
    for k in keys:
        path = os.path.join(CACHE_DIR, k + ".png")
        if os.path.isfile(path):
            try:
                img = Image.open(path).convert("RGBA")
                _mem[primary] = img
                return img
            except Exception:
                pass

    urls = []
    for k in keys:
        urls.append(URL.format(k))
        urls.append(GURL.format(k))

    for u in urls:
        try:
            r = httpx.get(u, timeout=6, follow_redirects=True)
            if r.status_code == 200 and r.content:
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                save_path = os.path.join(CACHE_DIR, primary + ".png")
                open(save_path, "wb").write(r.content)
                _mem[primary] = img
                return img
        except Exception:
            pass

    _mem[primary] = None
    return None

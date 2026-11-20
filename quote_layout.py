"""/q layout helpers: fonts, word wrap, inline emoji drawing (thread-safe)."""
import os
import re
import threading

from PIL import Image, ImageDraw, ImageFont

import quote_emoji as qe
import quote_fb as qf

TERMUX = "/data/data/com.termux/files/usr/share/fonts/TTF/"
SYS_DJV = "/usr/share/fonts/truetype/dejavu/"

REG = [
    "assets/fonts/DejaVuSans.ttf",
    TERMUX + "DejaVuSans.ttf",
    SYS_DJV + "DejaVuSans.ttf",
]
BOLD = [
    "assets/fonts/DejaVuSans-Bold.ttf",
    TERMUX + "DejaVuSans-Bold.ttf",
    SYS_DJV + "DejaVuSans-Bold.ttf",
]
ITALIC = [
    "assets/fonts/DejaVuSans-Oblique.ttf",
    "assets/fonts/DejaVuSans-Italic.ttf",
    TERMUX + "DejaVuSans-Oblique.ttf",
    SYS_DJV + "DejaVuSans-Oblique.ttf",
] + REG
BOLD_ITALIC = [
    "assets/fonts/DejaVuSans-BoldOblique.ttf",
    "assets/fonts/DejaVuSans-BoldItalic.ttf",
    TERMUX + "DejaVuSans-BoldOblique.ttf",
    SYS_DJV + "DejaVuSans-BoldOblique.ttf",
] + BOLD
MONO = [
    "assets/fonts/DejaVuSansMono.ttf",
    TERMUX + "DejaVuSansMono.ttf",
    SYS_DJV + "DejaVuSansMono.ttf",
] + REG
MONO_BOLD = [
    "assets/fonts/DejaVuSansMono-Bold.ttf",
    TERMUX + "DejaVuSansMono-Bold.ttf",
    SYS_DJV + "DejaVuSansMono-Bold.ttf",
] + MONO

ES = 26  # emoji picture size
_LOCK = threading.RLock()
_D = ImageDraw.Draw(Image.new("RGB", (1, 1)))
_FONT_CACHE: dict[tuple[tuple[str, ...], int], ImageFont.FreeTypeFont] = {}


def font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    size = max(1, int(round(size)))
    key = (tuple(paths), size)
    with _LOCK:
        if key in _FONT_CACHE:
            return _FONT_CACHE[key]
        for p in paths:
            if os.path.isfile(p):
                f = ImageFont.truetype(p, size)
                _FONT_CACHE[key] = f
                return f
        f = ImageFont.load_default(size)
        _FONT_CACHE[key] = f
        return f


def style_font(size: int, bold: bool = False, italic: bool = False, mono: bool = False):
    """Return the matching PIL ImageFont for a combination of styles."""
    if mono:
        return font(MONO_BOLD if bold else MONO, size)
    if bold and italic:
        return font(BOLD_ITALIC, size)
    if bold:
        return font(BOLD, size)
    if italic:
        return font(ITALIC, size)
    return font(REG, size)


def tw(t: str, f) -> float:
    return qf.tw(t, f)


def tokens(text: str, f, avail: float):
    out = []
    for kind, val in qe.split(text):
        if kind == "e":
            out.append(("e", val, int(getattr(f, "size", 26) * 1.1) + 3))
            continue
        for p in re.findall(r"\n|[^\S\n]+|\S+", val):
            if p == "\n":
                out.append(("n", p, 0))
            elif p.isspace():
                out.append(("s", " ", tw(" ", f)))
            else:
                while tw(p, f) > avail and len(p) > 1:
                    k = len(p)
                    while k > 1 and tw(p[:k], f) > avail:
                        k -= 1
                    out.append(("w", p[:k], tw(p[:k], f)))
                    out.append(("n", "", 0))
                    p = p[k:]
                out.append(("w", p, tw(p, f)))
    return out


def layout(toks, avail: float):
    lines, cur, cw = [], [], 0.0
    for t in toks:
        if t[0] == "n":
            lines.append(cur)
            cur, cw = [], 0.0
        elif t[0] == "s" and not cur:
            continue
        elif cw + t[2] > avail and cur and t[0] != "s":
            lines.append(cur)
            cur, cw = [t], t[2]
        else:
            cur.append(t)
            cw += t[2]
    lines.append(cur)
    for ln in lines:
        while ln and ln[-1][0] == "s":
            ln.pop()
    return lines


def row_w(line) -> float:
    return sum(t[2] for t in line)


def draw_row(img: Image.Image, dr: ImageDraw.ImageDraw, x: float, y: int, line, f, fill) -> None:
    for kind, val, w in line:
        if kind == "e":
            em = qe.fetch(val)
            if em is not None:
                em = em.resize((int(getattr(f, "size", 26) * 1.1),) * 2, Image.LANCZOS)
                img.paste(em, (int(x) + 1, y + 2), em)
        elif kind == "w":
            qf.draw(dr, x, y, val, f, fill)
        x += w

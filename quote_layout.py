"""/q layout helpers: fonts, word wrap, inline emoji drawing."""
import os
import re

from PIL import Image, ImageDraw, ImageFont

import quote_emoji as qe

TERMUX = "/data/data/com.termux/files/usr/share/fonts/TTF/"
BOLD = ["assets/fonts/DejaVuSans-Bold.ttf", TERMUX + "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
REG = ["assets/fonts/DejaVuSans.ttf", TERMUX + "DejaVuSans.ttf",
       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
ES = 26  # emoji picture size
_D = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def font(paths, size):
    for p in paths:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def tw(t, f):
    return _D.textlength(t, font=f)


def tokens(text, f, avail):
    out = []
    for kind, val in qe.split(text):
        if kind == "e":
            out.append(("e", val, ES + 3))
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


def layout(toks, avail):
    lines, cur, cw = [], [], 0
    for t in toks:
        if t[0] == "n":
            lines.append(cur)
            cur, cw = [], 0
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


def row_w(line):
    return sum(t[2] for t in line)


def draw_row(img, dr, x, y, line, f, fill):
    for kind, val, w in line:
        if kind == "e":
            em = qe.fetch(val)
            if em is not None:
                em = em.resize((ES, ES), Image.LANCZOS)
                img.paste(em, (int(x) + 1, y + 2), em)
        elif kind == "w":
            dr.text((x, y), val, font=f, fill=fill)
        x += w

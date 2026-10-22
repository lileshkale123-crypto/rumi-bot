"""/q fallback fonts: pick a font per character so stylish names show."""
import os
from PIL import Image, ImageDraw, ImageFont

FB = [
    "assets/fonts/NotoSansDevanagari.ttf",
    "assets/fonts/NotoSansSymbols.ttf",
    "assets/fonts/NotoSansSymbols2.ttf",
    "assets/fonts/NotoCJKsub.otf",
]
ZW = "\u200b\u200c\u200d\ufe0e\ufe0f"
_D = ImageDraw.Draw(Image.new("RGB", (1, 1)))
_REF = {}
_COV = {}
_FBF = {}


def _bits(f, ch):
    im = Image.new("L", (96, 96), 0)
    ImageDraw.Draw(im).text((8, 8), ch, font=f, fill=255)
    return im.tobytes()


def _has(path, ch):
    k = (path, ch)
    r = _COV.get(k)
    if r is None:
        ref = _REF.get(path)
        if ref is None:
            f = ImageFont.truetype(path, 32)
            ref = _REF[path] = (f, _bits(f, "\uffff"))
        b = _bits(ref[0], ch)
        r = _COV[k] = (b != ref[1] and any(b))
    return r


def pick(f, ch):
    path = getattr(f, "path", None)
    if not isinstance(path, str) or ("DejaVu" not in path and "NotoSans" not in path):
        return f
    if ch.isspace() or _has(path, ch):
        return f
    for p in FB:
        if os.path.isfile(p) and _has(p, ch):
            k = (p, int(getattr(f, "size", 26)))
            if k not in _FBF:
                _FBF[k] = ImageFont.truetype(p, k[1])
            return _FBF[k]
    return f


def covered(ch):
    try:
        for p in ["assets/fonts/DejaVuSans.ttf"] + FB:
            if os.path.isfile(p) and _has(p, ch):
                return True
    except Exception:
        pass
    return False


def runs(text, f):
    try:
        out = []
        for ch in text:
            if ch in ZW:
                continue
            g = pick(f, ch)
            if out and out[-1][1] is g:
                out[-1][0] += ch
            else:
                out.append([ch, g])
        return [(s, g) for s, g in out]
    except Exception:
        return [(text, f)]


def tw(t, f):
    return sum(_D.textlength(s, font=g) for s, g in runs(t, f))


def draw(dr, x, y, val, f, fill):
    for s, g in runs(val, f):
        if g is f:
            dr.text((x, y), s, font=f, fill=fill)
        else:
            base = y + f.getmetrics()[0]
            dr.text((x, base), s, font=g, fill=fill, anchor="ls")
        x += _D.textlength(s, font=g)


def draw_baseline(dr, x, baseline_y, val, f, fill):
    """Draw text anchored at the alphabetic baseline (ls) across fallback fonts."""
    for s, g in runs(val, f):
        dr.text((x, baseline_y), s, font=g, fill=fill, anchor="ls")
        x += _D.textlength(s, font=g)
    return x


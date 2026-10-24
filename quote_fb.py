"""/q fallback fonts: pick a font per character so stylish names, Hindi, CJK, and ꧁꧂ wings show."""
import os
import unicodedata
from PIL import Image, ImageDraw, ImageFont

FB = [
    "assets/fonts/NotoSansDevanagari.ttf",
    "assets/fonts/NotoSansMath.ttf",
    "assets/fonts/NotoSerifTibetan.ttf",
    "assets/fonts/NotoSansSymbols.ttf",
    "assets/fonts/NotoSansSymbols2.ttf",
    "assets/fonts/NotoCJKsub.otf",
]
ZW = "\u200b\u200c\u200d\ufe0e\ufe0f\u2060\ufeff"
_D = ImageDraw.Draw(Image.new("RGB", (1, 1)))
_REF = {}
_COV = {}
_FBF = {}


def _bits(f, ch):
    im = Image.new("L", (96, 96), 0)
    try:
        ImageDraw.Draw(im).text((8, 8), ch, font=f, fill=255)
    except Exception:
        return b""
    return im.tobytes()


def _has(path, ch):
    k = (path, ch)
    r = _COV.get(k)
    if r is None:
        if not os.path.isfile(path):
            _COV[k] = False
            return False
        ref = _REF.get(path)
        if ref is None:
            f = ImageFont.truetype(path, 32)
            # Compare against both \uffff and \ufffe to catch any .notdef tofu box
            ref = _REF[path] = (f, _bits(f, "\uffff"), _bits(f, "\ufffe"))
        b = _bits(ref[0], ch)
        r = _COV[k] = (b != ref[1] and b != ref[2] and any(b))
    return r


def pick(f, ch):
    path = getattr(f, "path", None)
    if not isinstance(path, str):
        return f
    if ch.isspace() or _has(path, ch):
        return f
    size = int(getattr(f, "size", 26))
    for p in ["assets/fonts/DejaVuSans.ttf"] + FB:
        if os.path.isfile(p) and _has(p, ch):
            k = (p, size)
            if k not in _FBF:
                _FBF[k] = ImageFont.truetype(p, size)
            return _FBF[k]
    return None  # Signals character is not in any font -> normalize or skip tofu


def covered(ch):
    try:
        for p in [
            "assets/fonts/DejaVuSans.ttf",
            "assets/fonts/DejaVuSans-Bold.ttf",
            "assets/fonts/DejaVuSans-Oblique.ttf",
        ] + FB:
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
            if g is None:
                # Try NFKC normalization if glyph is missing in all fonts
                norm = unicodedata.normalize("NFKC", ch)
                if norm and norm != ch:
                    for nch in norm:
                        ng = pick(f, nch) or f
                        if out and out[-1][1] is ng:
                            out[-1][0] += nch
                        else:
                            out.append([nch, ng])
                continue
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

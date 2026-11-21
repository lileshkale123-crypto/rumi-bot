"""/q fallback fonts: pick a font per character so stylish text never shows empty boxes.

The main font (DejaVu / Noto) is tried first. For any character it cannot draw, the
fallback fonts in assets/fonts are tried one by one. Missing font files are skipped, so
this works with whatever is installed (run fetch_quote_assets.py to add more).
Thread-safe, and every cache is bounded so RAM stays small.
"""
import os
import threading
import unicodedata
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = "assets/fonts"

# Best-looking first. Anything else found in assets/fonts is appended automatically,
# bitmap-style "unifont" files always go last.
_PREFERRED = [
    "NotoSans-Regular.ttf",
    "NotoSansDevanagari.ttf",
    "NotoSansSymbols.ttf",
    "NotoSansSymbols2.ttf",
    "NotoSansMath.ttf",
    "NotoSansArabic-Regular.ttf",
    "NotoSansHebrew-Regular.ttf",
    "NotoSansBengali-Regular.ttf",
    "NotoSansGurmukhi-Regular.ttf",
    "NotoSansGujarati-Regular.ttf",
    "NotoSansTamil-Regular.ttf",
    "NotoSansTelugu-Regular.ttf",
    "NotoSansKannada-Regular.ttf",
    "NotoSansMalayalam-Regular.ttf",
    "NotoSansSinhala-Regular.ttf",
    "NotoSansThai-Regular.ttf",
    "NotoSansLao-Regular.ttf",
    "NotoSansKhmer-Regular.ttf",
    "NotoSansMyanmar-Regular.ttf",
    "NotoSansGeorgian-Regular.ttf",
    "NotoSansArmenian-Regular.ttf",
    "NotoSansEthiopic-Regular.ttf",
    "NotoSansCherokee-Regular.ttf",
    "NotoSansCanadianAboriginal-Regular.ttf",
    "NotoSansTaiViet-Regular.ttf",
    "NotoSansJavanese-Regular.ttf",
    "NotoSansBalinese-Regular.ttf",
    "NotoSansRunic-Regular.ttf",
    "NotoSansOgham-Regular.ttf",
    "NotoSansBraille-Regular.ttf",
    "NotoSerifTibetan.ttf",
    "NotoCJKsub.otf",
]
_MAIN_PREFIXES = ("DejaVuSans",)  # the main text fonts, never used as fallback
ZW = "\U0000200B\U0000200C\U0000200D\U0000180E\U0000FE0E\U0000FE0F"

_OPEN_MAX = 40  # fonts kept open at once (RAM)
_COV_MAX = 30_000

_LOCK = threading.RLock()
_D = ImageDraw.Draw(Image.new("RGB", (1, 1)))
_REF: "OrderedDict[str, tuple[ImageFont.FreeTypeFont, bytes]]" = OrderedDict()
_COV: "dict[tuple[str, str], bool]" = {}
_PICKED: "OrderedDict[tuple[str, str], str | None]" = OrderedDict()
_FBF: "OrderedDict[tuple[str, int], ImageFont.FreeTypeFont]" = OrderedDict()
_FBF_MAX = 48


def _discover() -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in _PREFERRED:
        p = os.path.join(FONT_DIR, name)
        if os.path.isfile(p):
            out.append(p)
            seen.add(name)
    try:
        extra = sorted(os.listdir(FONT_DIR))
    except OSError:
        extra = []
    late = []
    for name in extra:
        if name in seen or not name.lower().endswith((".ttf", ".otf")):
            continue
        if name.startswith(_MAIN_PREFIXES):
            continue
        if "unifont" in name.lower():
            late.append(os.path.join(FONT_DIR, name))
        else:
            out.append(os.path.join(FONT_DIR, name))
    return out + late


# Public list: other modules may read or extend it.
FB: list[str] = _discover()


def _bits(f: ImageFont.FreeTypeFont, ch: str) -> bytes:
    im = Image.new("L", (96, 96), 0)
    ImageDraw.Draw(im).text((8, 8), ch, font=f, fill=255)
    return im.tobytes()


def _ref(path: str):
    r = _REF.get(path)
    if r is None:
        f = ImageFont.truetype(path, 32)
        r = (f, _bits(f, "\U0000FFFF"))
        _REF[path] = r
        while len(_REF) > _OPEN_MAX:
            _REF.popitem(last=False)
    else:
        _REF.move_to_end(path)
    return r


def _has(path: str, ch: str) -> bool:
    with _LOCK:
        k = (path, ch)
        r = _COV.get(k)
        if r is None:
            try:
                f, notdef = _ref(path)
                b = _bits(f, ch)
                r = b != notdef and any(b)
            except Exception:
                r = False
            if len(_COV) >= _COV_MAX:
                _COV.clear()
            _COV[k] = r
        return r


def _sized(path: str, size: int) -> ImageFont.FreeTypeFont:
    k = (path, size)
    f = _FBF.get(k)
    if f is None:
        f = ImageFont.truetype(path, size)
        _FBF[k] = f
        while len(_FBF) > _FBF_MAX:
            _FBF.popitem(last=False)
    else:
        _FBF.move_to_end(k)
    return f


def pick(f: ImageFont.FreeTypeFont, ch: str) -> ImageFont.FreeTypeFont:
    """The font that can draw `ch`: `f` itself if it can, else the first fallback that can."""
    path = getattr(f, "path", None)
    if not isinstance(path, str) or ("DejaVu" not in path and "Noto" not in path):
        return f
    if ch.isspace():
        return f
    size = int(getattr(f, "size", 26))
    with _LOCK:
        key = (path, ch)
        if key in _PICKED:
            fb = _PICKED[key]
            _PICKED.move_to_end(key)
            return f if fb is None else _sized(fb, size)
        if _has(path, ch):
            fb = None
        else:
            fb = None
            for p in FB:
                if p != path and os.path.isfile(p) and _has(p, ch):
                    fb = p
                    break
        _PICKED[key] = fb
        while len(_PICKED) > _COV_MAX:
            _PICKED.popitem(last=False)
        return f if fb is None else _sized(fb, size)


def covered(ch: str) -> bool:
    """True if the main font or any fallback font can draw `ch`."""
    try:
        for p in [os.path.join(FONT_DIR, "DejaVuSans.ttf")] + FB:
            if os.path.isfile(p) and _has(p, ch):
                return True
    except Exception:
        pass
    return False


def runs(text: str, f: ImageFont.FreeTypeFont):
    try:
        out = []
        for ch in text:
            if ch in ZW:
                continue
            if out and unicodedata.category(ch) in ("Mn", "Mc", "Me"):
                out[-1][0] += ch  # marks (matras, virama) stay with their letter
                continue
            g = pick(f, ch)
            if out and out[-1][1] is g:
                out[-1][0] += ch
            else:
                out.append([ch, g])
        return [(s, g) for s, g in out]
    except Exception:
        return [(text, f)]


def tw(t: str, f: ImageFont.FreeTypeFont) -> float:
    with _LOCK:
        return sum(_D.textlength(s, font=g) for s, g in runs(t, f))


def draw(dr: ImageDraw.ImageDraw, x: float, y: float, val: str, f: ImageFont.FreeTypeFont, fill) -> None:
    for s, g in runs(val, f):
        if g is f:
            dr.text((x, y), s, font=f, fill=fill)
        else:
            base = y + f.getmetrics()[0]
            dr.text((x, base), s, font=g, fill=fill, anchor="ls")
        with _LOCK:
            x += _D.textlength(s, font=g)


def draw_baseline(dr: ImageDraw.ImageDraw, x: float, baseline_y: float, val: str, f: ImageFont.FreeTypeFont, fill) -> float:
    """Draw text anchored at the alphabetic baseline (ls) across fallback fonts."""
    for s, g in runs(val, f):
        dr.text((x, baseline_y), s, font=g, fill=fill, anchor="ls")
        with _LOCK:
            x += _D.textlength(s, font=g)
    return x

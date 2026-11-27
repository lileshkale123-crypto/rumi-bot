"""/q fallback fonts: pick a font per character so stylish text never shows empty boxes.

The main font (DejaVu / Noto) is tried first. For any character it cannot draw, the
fallback fonts in assets/fonts are tried one by one. Missing font files are skipped, so
this works with whatever is installed (run fetch_quote_assets.py to add more).
Thread-safe, and every cache is bounded so RAM stays small.
"""
import bisect
import os
import struct
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


_CMAPS: "dict[str, tuple[list[int], list[int]] | None]" = {}


def _read_cmap(path: str):
    """The font's real character table, read straight from the file (standard library only).

    Returns (starts, ends): sorted code point ranges the font really has a glyph for, or None
    when the file cannot be read (then the old bitmap test is used for that font).
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            base = 0
            if head[:4] == b"ttcf":
                fh.seek(12)
                base = struct.unpack(">I", fh.read(4))[0]
                fh.seek(base)
                head = fh.read(12)
            num = struct.unpack(">H", head[4:6])[0]
            fh.seek(base + 12)
            tab = None
            for _ in range(num):
                rec = fh.read(16)
                if rec[:4] == b"cmap":
                    tab = struct.unpack(">II", rec[8:16])
                    break
            if tab is None:
                return None
            fh.seek(tab[0])
            data = fh.read(tab[1])
        count = struct.unpack_from(">H", data, 2)[0]
        pairs: list[tuple[int, int]] = []
        done: set[int] = set()
        for i in range(count):
            plat, enc, off = struct.unpack_from(">HHI", data, 4 + 8 * i)
            if not (plat == 0 or (plat == 3 and enc in (1, 10))) or off in done:
                continue
            done.add(off)
            fmt = struct.unpack_from(">H", data, off)[0]
            if fmt == 4:
                segx2 = struct.unpack_from(">H", data, off + 6)[0]
                seg = segx2 // 2
                ends = struct.unpack_from(">%dH" % seg, data, off + 14)
                starts = struct.unpack_from(">%dH" % seg, data, off + 16 + segx2)
                deltas = struct.unpack_from(">%dh" % seg, data, off + 16 + 2 * segx2)
                ro_pos = off + 16 + 3 * segx2
                ranges = struct.unpack_from(">%dH" % seg, data, ro_pos)
                for k in range(seg):
                    a, b = starts[k], ends[k]
                    if a > b or a == 0xFFFF:
                        continue
                    if ranges[k] == 0:
                        zero = (-deltas[k]) & 0xFFFF  # the one code point that would map to glyph 0
                        if a <= zero <= b:
                            if a <= zero - 1:
                                pairs.append((a, zero - 1))
                            if zero + 1 <= b:
                                pairs.append((zero + 1, b))
                        else:
                            pairs.append((a, b))
                    else:
                        run = None
                        for c in range(a, b + 1):
                            pos = ro_pos + 2 * k + ranges[k] + 2 * (c - a)
                            g = struct.unpack_from(">H", data, pos)[0] if pos + 2 <= len(data) else 0
                            ok = g != 0 and ((g + deltas[k]) & 0xFFFF) != 0
                            if ok and run is None:
                                run = c
                            elif not ok and run is not None:
                                pairs.append((run, c - 1))
                                run = None
                        if run is not None:
                            pairs.append((run, b))
            elif fmt == 6:
                first, cnt = struct.unpack_from(">HH", data, off + 6)
                gl = struct.unpack_from(">%dH" % cnt, data, off + 10)
                for k, g in enumerate(gl):
                    if g:
                        pairs.append((first + k, first + k))
            elif fmt in (12, 13):
                ng = struct.unpack_from(">I", data, off + 12)[0]
                for k in range(ng):
                    a, b, g = struct.unpack_from(">III", data, off + 16 + 12 * k)
                    if fmt == 12 and g == 0:
                        a += 1
                    if fmt == 13 and g == 0:
                        continue
                    if a <= b:
                        pairs.append((a, b))
        if not pairs:
            return None
        pairs.sort()
        starts_o: list[int] = []
        ends_o: list[int] = []
        for a, b in pairs:
            if ends_o and a <= ends_o[-1] + 1:
                ends_o[-1] = max(ends_o[-1], b)
            else:
                starts_o.append(a)
                ends_o.append(b)
        return starts_o, ends_o
    except Exception:
        return None


def _in_cmap(path: str, cp: int):
    """True / False from the font's real table, or None if the table could not be read."""
    if path not in _CMAPS:
        _CMAPS[path] = _read_cmap(path)
    cm = _CMAPS[path]
    if cm is None:
        return None
    starts, ends = cm
    i = bisect.bisect_right(starts, cp) - 1
    return i >= 0 and cp <= ends[i]


def _has(path: str, ch: str) -> bool:
    with _LOCK:
        k = (path, ch)
        r = _COV.get(k)
        if r is None:
            r = _in_cmap(path, ord(ch[0])) if ch else False
            if r is None:  # table unreadable: fall back to the old bitmap comparison
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


def covers(font, ch: str) -> bool:
    """True if this exact font file really has a glyph for `ch`."""
    path = getattr(font, "path", None)
    return isinstance(path, str) and _has(path, ch)


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

"""Complete Quotly quote-api renderer in pure Python (Pillow) with all 7 polish fixes."""
import io
import math
import os
import re
import unicodedata
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

import quote_emoji as qe
import quote_fb as qf

# ---- Exact Constants from constants.js, styles.js & accent-colors.js -------

NAME_COLORS_LIGHT = [
    "#FC5C51", "#FA790F", "#895DD5", "#0FB297", "#0FC9D6", "#3CA5EC", "#D54FAF"
]
NAME_COLORS_DARK = [
    "#FF8E86", "#FFA357", "#B18FFF", "#4DD6BF", "#45E8D1", "#7AC9FF", "#FF7FD5"
]
AVATAR_COLORS = [
    ("#FF885E", "#FF516A"), ("#FFCD6A", "#FFA85C"), ("#E0A2F3", "#D669ED"),
    ("#A0DE7E", "#54CB68"), ("#53EDD6", "#28C9B7"), ("#72D5FD", "#2A9EF1"),
    ("#FFA8A8", "#FF719A"),
]
TAG_COLORS = {
    "owner": {"dark": "#b08cf0", "light": "#6d3fc0"},
    "admin": {"dark": "#6fcb6d", "light": "#2c8a36"},
}

ACCENT_LIGHT = {
    0: ["#cc5049"], 1: ["#d67722"], 2: ["#955cdb"], 3: ["#40a920"], 4: ["#309eba"], 5: ["#368ad1"], 6: ["#c7508b"],
    7: ["#e15052", "#f9ae63"], 8: ["#e0802b", "#fac534"], 9: ["#a05ff3", "#f48fff"],
    10: ["#27a910", "#a7dc57"], 11: ["#27acce", "#82e8d6"], 12: ["#3391d4", "#7dd3f0"], 13: ["#dd4371", "#ffbe9f"],
    14: ["#247bed", "#f04856", "#ffffff"], 15: ["#d67722", "#1ea011", "#ffffff"], 16: ["#179e42", "#e84a3f", "#ffffff"],
    17: ["#2894af", "#6fc456", "#ffffff"], 18: ["#0c9ab3", "#ffad95", "#ffe6b5"], 19: ["#7757d6", "#f79610", "#ffde8e"],
    20: ["#1585cf", "#f2ab1d", "#ffffff"],
}
ACCENT_DARK = {
    0: ["#ff8e86"], 1: ["#ffa357"], 2: ["#b18fff"], 3: ["#4dd6bf"], 4: ["#45e8d1"], 5: ["#7ac9ff"], 6: ["#ff7fd5"],
    7: ["#ff9380", "#992f37"], 8: ["#ecb04e", "#c35714"], 9: ["#c697ff", "#5e31c8"],
    10: ["#a7eb6e", "#167e2d"], 11: ["#40d8d0", "#045c7f"], 12: ["#52bfff", "#0b5494"], 13: ["#ff86a6", "#8e366e"],
    14: ["#3fa2fe", "#e5424f", "#ffffff"], 15: ["#ff905e", "#32a527", "#ffffff"], 16: ["#66d364", "#d5444f", "#ffffff"],
    17: ["#22bce2", "#3da240", "#ffffff"], 18: ["#22bce2", "#ff9778", "#ffda6b"], 19: ["#9791ff", "#f2731d", "#ffdb59"],
    20: ["#3da6eb", "#eea51d", "#ffffff"],
}

EMOJI_CELLS = [
    {"dx": 14, "dy": 0.28, "size": 14},
    {"dx": 36, "dy": 0.72, "size": 12},
    {"dx": 40, "dy": 0.18, "size": 10},
    {"dx": 62, "dy": 0.50, "size": 13},
    {"dx": 88, "dy": 0.20, "size": 9},
    {"dx": 92, "dy": 0.80, "size": 10},
    {"dx": 120, "dy": 0.50, "size": 8},
]

ENTITY_TYPES_MONOSPACE = {"pre", "code", "pre_code"}
ENTITY_TYPES_MENTION = {
    "mention", "text_mention", "hashtag", "cashtag",
    "email", "phone_number", "bot_command", "url", "text_link",
}
EMOJI_SCALE = 1.15

CJK_RE = re.compile(
    r"[\u1100-\u11ff\u2e80-\u2eff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff"
    r"\u3100-\u312f\u3130-\u318f\u3190-\u319f\u31a0-\u31bf\u31c0-\u31ef"
    r"\u31f0-\u31ff\u3200-\u32ff\u3300-\u33ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uac00-\ud7af\uf900-\ufaff]"
)
RTL_RE = re.compile(r"[\u0591-\u07FF\u200F\u202B\u202E\uFB1D-\uFDFD\uFE70-\uFEFC]")
TALL_SCRIPT_RE = re.compile(r"[\u0590-\u1dff\u2e80-\uffef]")

LEFT_STICKY_PUNCTUATION = {
    ".", ",", "!", "?", ":", ";", ")", "]", "}", "%", '"', "'",
    "\u2019", "\u201d", "\u2026", "\u3001", "\u3002", "\uff0c", "\uff0e", "\uff01", "\uff09", "\uff3d",
}
KINSOKU_START = {
    "\u3001", "\u3002", "\uff0c", "\uff0e", "\u3005", "\u3009", "\u300b", "\u300d", "\u300f", "\u3011",
    "\u301f", "\u30fc", "\u30fd", "\u30fe", "\u309d", "\u309e", "\uff09", "\uff3d", "\uff5d", "\uff01", "\uff1f",
}

GLASS_STYLE = {
    "inset_x": 16, "inset_y": 16, "stack_tight": 12, "stack": 12, "inline": 8,
    "chip": 8, "max_header": 300, "max_lines": 16, "radius": 25, "radius_grouped": 7,
    "min_radius": 4, "block_radius": 9, "chip_radius": 17, "reply_thumb": 40,
    "shadow_pad": 12, "shadow_pad_top": 4, "glass_lw": 2, "tail_size": 14,
    "min_width": 100, "avatar": 50, "avatar_gap": 10,
    "font_body": 24, "font_name": 20, "font_reply": 19, "font_micro": 15,
    "micro_alpha": 0.65,
    "block_pad_y": 8, "block_pad_l": 12, "block_pad_r": 12, "block_pad_r_icon": 32,
    "block_bar": 4, "block_icon": 16, "block_icon_inset": 4, "block_tint": 0.10,
    "block_gap": 8, "block_thumb_gap": 8, "block_thumb_radius": 4,
    "stripe_dash": 6, "stripe_slant": 4, "emoji_alpha": 0.14,
    "tag_pad_x": 8, "tag_pad_y": 4, "tag_tint": 0.15,
    "row_disc": 44, "row_gap": 12, "row_title": 18, "row_line_gap": 8,
    "row_bar": 3, "row_bar_gap": 2, "row_bar_min": 4, "row_bar_max": 28, "row_cover": 9,
    "badge_pad_x": 8, "badge_pad_y": 4, "badge_bg": 0.62, "badge_inset": 8, "badge_play": 48,
    "shadow_blur": 6, "shadow_y": 2, "shadow_alpha": 0.24,
}


# ---- Color Math (utils/quote-generate/color.js & accent-colors.js) ---------

def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = str(hex_str or "").strip().lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    if len(h) != 6:
        return (41, 34, 50)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (41, 34, 50)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = [max(0, min(255, int(round(c)))) for c in rgb[:3]]
    return f"#{r:02x}{g:02x}{b:02x}"


def rgba_tuple(hex_str: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    r, g, b = hex_to_rgb(hex_str)
    return (r, g, b, max(0, min(255, int(round(alpha * 255)))))


def color_luminance(hex_str: str, lum: float = 0.0) -> str:
    h = "".join(c for c in str(hex_str) if c.lower() in "0123456789abcdef")
    if len(h) < 6:
        h = h[0]*2 + h[1]*2 + h[2]*2 if len(h) >= 3 else "292232"
    rgb = "#"
    for i in range(3):
        c = int(h[i*2 : i*2 + 2], 16)
        c = round(min(max(0, c + (c * lum)), 255))
        rgb += f"{c:02x}"
    return rgb


def light_or_dark(color: str) -> str:
    r, g, b = hex_to_rgb(color)
    hsp = math.sqrt(0.299 * (r * r) + 0.587 * (g * g) + 0.114 * (b * b))
    return "light" if hsp > 127.5 else "dark"


def get_brightness(color: str) -> float:
    r, g, b = hex_to_rgb(color)
    return (r * 299 + g * 587 + b * 114) / 1000.0


def adjust_brightness(color: str, amount: float) -> str:
    r, g, b = hex_to_rgb(color)
    return rgb_to_hex((r + amount, g + amount, b + amount))


def get_contrast_ratio(bg: str, fg: str) -> float:
    b1, b2 = get_brightness(bg), get_brightness(fg)
    return (max(b1, b2) + 0.05) / (min(b1, b2) + 0.05)


def adjust_contrast(bg: str, fg: str) -> str:
    ratio = get_contrast_ratio(bg, fg)
    diff = get_brightness(bg) - get_brightness(fg)
    if ratio >= 4.5:
        return fg
    if diff >= 0:
        return adjust_brightness(fg, math.ceil((175.0 - get_brightness(fg)) / 2.0))
    return adjust_brightness(fg, -math.ceil((get_brightness(fg) - 175.0) / 2.0))


def resolve_accent(accent_id: int | None, is_light: bool) -> list[str] | None:
    if not isinstance(accent_id, int):
        return None
    table = ACCENT_LIGHT if is_light else ACCENT_DARK
    colors = table.get(accent_id)
    if not colors:
        return None
    if is_light:
        out = []
        for idx, c in enumerate(colors):
            r, g, b = hex_to_rgb(c)
            lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
            if idx == 0 or lum < 0.9:
                out.append(c)
        return out
    return list(colors)


def parse_background_color(background_color: str | None = None) -> dict:
    if not isinstance(background_color, str) or not background_color.strip():
        background_color = "//#292232"
    background_color = background_color.strip()
    split = background_color.split("/")
    if len(split) > 1 and split[0] != "":
        return {"colorOne": split[0], "colorTwo": split[1]}
    elif background_color.startswith("//"):
        base = background_color.replace("//", "", 1)
        return {"colorOne": color_luminance(base, 0.35), "colorTwo": color_luminance(base, -0.15)}
    else:
        return {"colorOne": background_color, "colorTwo": background_color}


# ---- Font Loader & Fallback Drawing (Hindi + CJK + Symbols) ----------------

_FONT_CACHE = {}
_DUMMY_DR = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

if hasattr(qf, "FB") and "assets/fonts/NotoSansDevanagari.ttf" not in qf.FB:
    qf.FB.insert(0, "assets/fonts/NotoSansDevanagari.ttf")


def _load_style_font(size: int, styles: list[str] | set[str] = ()) -> ImageFont.FreeTypeFont:
    size = max(1, int(round(size)))
    bold = "bold" in styles
    italic = "italic" in styles
    mono = "monospace" in styles
    key = (size, bold, italic, mono)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    candidates = []
    if mono:
        if bold:
            candidates += [
                "assets/fonts/DejaVuSansMono-Bold.ttf",
                "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            ]
        candidates += [
            "assets/fonts/DejaVuSansMono.ttf",
            "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]
    if bold and italic:
        candidates += [
            "assets/fonts/DejaVuSans-BoldOblique.ttf",
            "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        ]
    if bold:
        candidates += [
            "assets/fonts/DejaVuSans-Bold.ttf",
            "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    if italic:
        candidates += [
            "assets/fonts/DejaVuSans-Oblique.ttf",
            "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ]
    candidates += [
        "assets/fonts/DejaVuSans.ttf",
        "assets/fonts/NotoSansDevanagari.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.isfile(p):
            f = ImageFont.truetype(p, size)
            _FONT_CACHE[key] = f
            return f
    f = ImageFont.load_default(size)
    _FONT_CACHE[key] = f
    return f


def _grapheme_clusters(text: str) -> list[str]:
    clusters = []
    for ch in text:
        if ch in qf.ZW:
            continue
        if clusters and (unicodedata.category(ch).startswith("M") or (0x0900 <= ord(ch) <= 0x097F and unicodedata.combining(ch))):
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


def _runs_with_fallback(text: str, f: ImageFont.FreeTypeFont) -> list[tuple[str, ImageFont.FreeTypeFont]]:
    out = []
    for cl in _grapheme_clusters(text):
        g = qf.pick(f, cl[0])
        if out and out[-1][1] is g:
            out[-1][0] += cl
        else:
            out.append([cl, g])
    return [(s, g) for s, g in out]


def _measure_text_width(text: str, f: ImageFont.FreeTypeFont) -> float:
    return sum(_DUMMY_DR.textlength(s, font=g) for s, g in _runs_with_fallback(text, f))


def _draw_text_baseline(dr: ImageDraw.ImageDraw, x: float, baseline_y: float, text: str, f: ImageFont.FreeTypeFont, fill: tuple) -> float:
    for s, g in _runs_with_fallback(text, f):
        dr.text((x, baseline_y), s, font=g, fill=fill, anchor="ls")
        x += _DUMMY_DR.textlength(s, font=g)
    return x


# ---- Optical & Font Metrics (canvas-utils.js & text-prepare.js) ------------

class QCanvas:
    def __init__(self, image: Image.Image, opt_t: float = 0, opt_b: float = 0, pill: bool = False):
        self.image = image
        self.width = image.width
        self.height = image.height
        self.optical = {"t": max(0, int(round(opt_t))), "b": max(0, int(round(opt_b)))}
        self.pill = pill
        self._tail_offset = 0


_METRICS_CACHE = {}
_CAP_CACHE = {}


def font_metrics(font_size: int) -> tuple[int, int]:
    font_size = int(round(font_size))
    if font_size in _METRICS_CACHE:
        return _METRICS_CACHE[font_size]
    f = _load_style_font(font_size)
    asc, desc = f.getmetrics()
    ascent = math.ceil(max(asc, font_size * 0.85))
    descent = math.ceil(max(desc, font_size * 0.30))
    _METRICS_CACHE[font_size] = (ascent, descent)
    return ascent, descent


def cap_height(font_size: int, bold: bool = False) -> int:
    font_size = int(round(font_size))
    key = (font_size, bool(bold))
    if key in _CAP_CACHE:
        return _CAP_CACHE[key]
    f = _load_style_font(font_size, ("bold",) if bold else ())
    try:
        bbox = _DUMMY_DR.textbbox((0, 0), "H", font=f, anchor="ls")
        cap = abs(bbox[1]) if bbox[1] < 0 else int(round(font_size * 0.714))
    except Exception:
        cap = int(round(font_size * 0.714))
    _CAP_CACHE[key] = cap
    return cap


def max_text_height(font_size: int, lines: int) -> float:
    asc, _ = font_metrics(font_size)
    return asc + (lines - 0.5) * (font_size * 1.2)


# ---- Bubble Path, Glass, Round Image, Quote Icon (canvas-utils.js) ---------

def _arc_points(cx, cy, r, start_deg, end_deg, steps=20):
    if r <= 0:
        return [(cx, cy)]
    return [
        (cx + r * math.cos(math.radians(start_deg + (end_deg - start_deg) * (i / steps))),
         cy + r * math.sin(math.radians(start_deg + (end_deg - start_deg) * (i / steps))))
        for i in range(steps + 1)
    ]


def _cubic_bezier(p0, p1, p2, p3, steps=28):
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        pts.append((
            (mt**3)*p0[0] + 3*(mt**2)*t*p1[0] + 3*mt*(t**2)*p2[0] + (t**3)*p3[0],
            (mt**3)*p0[1] + 3*(mt**2)*t*p1[1] + 3*mt*(t**2)*p2[1] + (t**3)*p3[1],
        ))
    return pts


def _quad_bezier(p0, p1, p2, steps=16):
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        pts.append((
            (mt**2)*p0[0] + 2*mt*t*p1[0] + (t**2)*p2[0],
            (mt**2)*p0[1] + 2*mt*t*p1[1] + (t**2)*p2[1],
        ))
    return pts


def bubble_polygon(w: float, h: float, r: dict | float, tail_size: float, ox: float = 0.0, oy: float = 0.0):
    if isinstance(r, (int, float)):
        tl = tr = br = bl = float(r)
    else:
        tl, tr, br, bl = float(r["tl"]), float(r["tr"]), float(r["br"]), float(r["bl"])
    cap = lambda v: min(v, w / 2.0, h / 2.0)
    tl, tr, br, bl = cap(tl), cap(tr), cap(br), cap(bl)

    pts = []
    pts.extend(_arc_points(ox + w - tr, oy + tr, tr, -90, 0))
    pts.extend(_arc_points(ox + w - br, oy + h - br, br, 0, 90))
    if tail_size > 0:
        t = float(tail_size)
        pts.append((ox - t, oy + h))
        pts.extend(_cubic_bezier((ox - t, oy + h), (ox - t * 0.4, oy + h), (ox, oy + h - bl * 0.3), (ox, oy + h - bl)))
    else:
        pts.extend(_arc_points(ox + bl, oy + h - bl, bl, 90, 180))
    pts.extend(_arc_points(ox + tl, oy + tl, tl, 180, 270))
    return pts


def _linear_gradient_2d(w: int, h: int, c1: tuple, c2: tuple) -> Image.Image:
    w, h = max(1, w), max(1, h)
    if c1[:3] == c2[:3]:
        return Image.new("RGBA", (w, h), (*c1[:3], 255))
    denom = float(w * w + h * h)
    grid = Image.new("RGBA", (64, 64))
    px = grid.load()
    for gy in range(64):
        y = gy * (h / 63.0)
        for gx in range(64):
            x = gx * (w / 63.0)
            t = max(0.0, min(1.0, (x * w + y * h) / denom))
            px[gx, gy] = (
                int(round(c1[0] + (c2[0] - c1[0]) * t)),
                int(round(c1[1] + (c2[1] - c1[1]) * t)),
                int(round(c1[2] + (c2[2] - c1[2]) * t)),
                255,
            )
    return grid.resize((w, h), Image.BICUBIC)


def draw_bubble(color_one: str, color_two: str, w: int, h: int, r: dict | float, tail_size: float = 0, glass_lw: float = 0) -> QCanvas:
    w, h = max(1, int(math.ceil(w))), max(1, int(math.ceil(h)))
    extra_left = int(math.ceil(tail_size * 0.8)) if tail_size > 0 else 0
    total_w = w + extra_left
    ss = 3
    sw, sh = total_w * ss, h * ss
    sr = {k: v * ss for k, v in r.items()} if isinstance(r, dict) else r * ss
    poly = bubble_polygon(w * ss, h * ss, sr, tail_size * ss, ox=extra_left * ss, oy=0)

    mask_ss = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask_ss).polygon(poly, fill=255)
    mask = mask_ss.resize((total_w, h), Image.LANCZOS)

    fill_img = _linear_gradient_2d(w, h, hex_to_rgb(color_one), hex_to_rgb(color_two))
    padded_fill = Image.new("RGBA", (total_w, h), (*hex_to_rgb(color_one), 255))
    padded_fill.paste(fill_img, (extra_left, 0))

    out = Image.new("RGBA", (total_w, h), (0, 0, 0, 0))
    out.paste(padded_fill, (0, 0), mask)

    if glass_lw > 0:
        pts_closed = poly + [poly[0]]
        border_ss = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        ImageDraw.Draw(border_ss).line(pts_closed, fill=(255, 255, 255, int(round(0.09 * 255))), width=max(1, int(round(glass_lw * 2 * ss))))
        top_ss = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        ImageDraw.Draw(top_ss).line(pts_closed, fill=(255, 255, 255, int(round(0.22 * 255))), width=max(1, int(round(glass_lw * 2.6 * ss))))
        fade_h = max(1, int(round(sh * 0.4)))
        grad_l = Image.new("L", (1, sh), 0)
        for y in range(fade_h):
            grad_l.putpixel((0, y), int(round(255 * (1.0 - y / fade_h))))
        grad_l = grad_l.resize((sw, sh), Image.NEAREST)
        top_ss.putalpha(ImageChops.multiply(top_ss.split()[3], grad_l))
        glass_ss = Image.alpha_composite(border_ss, top_ss)
        glass_ss.putalpha(ImageChops.multiply(glass_ss.split()[3], mask_ss))
        out = Image.alpha_composite(out, glass_ss.resize((total_w, h), Image.LANCZOS))

    qc = QCanvas(out, 0, 0)
    qc._tail_offset = extra_left
    return qc


def round_image(img: Image.Image, r: dict | float) -> Image.Image:
    w, h = img.size
    if isinstance(r, (int, float)):
        r = {"tl": r, "tr": r, "br": r, "bl": r}
    ss = 2
    sr = {k: v * ss for k, v in r.items()}
    poly = bubble_polygon(w * ss, h * ss, sr, 0, 0, 0)
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def cover_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    if w == h:
        return img.convert("RGBA")
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.convert("RGBA").crop((left, top, left + side, top + side))


def draw_quote_icon(size: int, color: str, alpha: float = 1.0) -> Image.Image:
    size = max(4, int(round(size)))
    ss = 3
    s = size * ss
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    fill = rgba_tuple(color, alpha)
    r = s * 0.09
    gap_x = s * 0.36
    for ox in (s * 0.08, s * 0.08 + gap_x):
        oy = s * 0.15
        cx, cy = ox + r, oy + r
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
        p0 = (ox + r * 2, cy)
        c1 = (ox + r * 2.2, oy + s * 0.35)
        p1 = (ox + r * 0.3, oy + s * 0.4)
        p2 = (ox + r * 0.3, oy + s * 0.32)
        c2 = (ox + r * 1.5, oy + s * 0.27)
        p3 = (ox + r * 0.8, cy)
        pts = [p0] + _quad_bezier(p0, c1, p1) + [p2] + _quad_bezier(p2, c2, p3)
        dr.polygon(pts, fill=fill)
    return im.resize((size, size), Image.LANCZOS)


def gradient_tint(qc: QCanvas, color_from: str, color_to: str) -> QCanvas:
    if not qc or qc.width < 2:
        return qc
    w, h = qc.width, qc.height
    c1, c2 = hex_to_rgb(color_from), hex_to_rgb(color_to)
    grad = Image.new("RGBA", (w, 1))
    for x in range(w):
        t = x / max(1, w - 1)
        grad.putpixel((x, 0), (
            int(round(c1[0] + (c2[0] - c1[0]) * t)),
            int(round(c1[1] + (c2[1] - c1[1]) * t)),
            int(round(c1[2] + (c2[2] - c1[2]) * t)),
            255,
        ))
    grad = grad.resize((w, h), Image.NEAREST)
    grad.putalpha(qc.image.split()[3])
    return QCanvas(grad, qc.optical["t"], qc.optical["b"], pill=qc.pill)


# ---- Avatar, Labels & Role Tags (avatar.js, canvas-utils.js, composer.js) --

def _extract_initials(name: str) -> str:
    clean = qe.clean(str(name or "")).strip()
    if not clean:
        return "?"
    words = [w for w in clean.upper().split() if w]
    if not words:
        return "?"
    c0 = _grapheme_clusters(words[0])
    first = c0[0] if c0 else "?"
    if len(words) > 1:
        c_last = _grapheme_clusters(words[-1])
        return first + (c_last[0] if c_last else "")
    return first


def draw_avatar(avatar_bytes: bytes | None, name: str, user_id: int, size: int) -> Image.Image:
    size = max(16, int(round(size)))
    ss = 2
    mask = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * ss - 1, size * ss - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)

    if avatar_bytes:
        try:
            src = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            fitted = ImageOps.fit(src, (size, size), Image.LANCZOS)
            out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            out.paste(fitted, (0, 0), mask)
            return out
        except Exception:
            pass

    idx = abs(int(user_id or 0)) % 7
    c1, c2 = AVATAR_COLORS[idx]
    bg = _linear_gradient_2d(size, size, hex_to_rgb(c1), hex_to_rgb(c2))
    dr = ImageDraw.Draw(bg)
    letters = _extract_initials(name)
    f_size = int(round(size * (0.38 if len(_grapheme_clusters(letters)) > 1 else 0.48)))
    f = _load_style_font(f_size, ("bold",))
    runs = _runs_with_fallback(letters, f)
    f_actual = runs[0][1] if runs else f
    dr.text((size / 2.0, size / 2.0), letters, font=f_actual, fill=(255, 255, 255, 255), anchor="mm")

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    return out


def draw_label(text: str, font_size: int, color: str, bold: bool = False, alpha: float = 1.0) -> QCanvas:
    font_size = int(round(font_size))
    text = qe.clean(str(text or ""))
    asc, desc = font_metrics(font_size)
    f = _load_style_font(font_size, ("bold",) if bold else ())
    w = max(1, int(math.ceil(_measure_text_width(text, f))))
    h = max(1, int(math.ceil(asc + desc)))
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    _draw_text_baseline(dr, 0, asc, text, f, rgba_tuple(color, alpha))
    return QCanvas(im, asc - cap_height(font_size, bold), h - asc)


def draw_tag(tag_tuple: tuple[str, str | None], s_fn, text_color: str) -> QCanvas | None:
    if not tag_tuple:
        return None
    role, custom_title = tag_tuple[0], tag_tuple[1] if len(tag_tuple) > 1 else None
    label_text = custom_title if custom_title else role
    if not label_text:
        return None

    size = s_fn(GLASS_STYLE["font_micro"])
    colors = TAG_COLORS.get(role)
    if not colors:
        return draw_label(label_text, size, text_color, alpha=GLASS_STYLE["micro_alpha"])

    color = colors["light"] if text_color == "#000" else colors["dark"]
    label = draw_label(label_text, size, color, bold=False)
    o = label.optical
    pad_x = s_fn(GLASS_STYLE["tag_pad_x"])
    pad_y = s_fn(GLASS_STYLE["tag_pad_y"])
    w = int(math.ceil(label.width + 2 * pad_x))
    h = int(math.ceil(label.height - o["t"] - o["b"] + 2 * pad_y))

    pill_bg = draw_bubble(color, color, w, h, h / 2.0, 0, 0).image
    r, g, b, a = pill_bg.split()
    pill_bg.putalpha(a.point(lambda p: int(round(p * GLASS_STYLE["tag_tint"]))))
    pill_bg.alpha_composite(label.image, (int(round(pad_x)), int(round(pad_y - o["t"]))))
    return QCanvas(pill_bg, 0, 0, pill=True)


# ---- Attachments, Badges & Media Spoiler (attachments.js & spoiler.js) -----

def format_duration(seconds: float | int) -> str:
    total = max(0, int(round(float(seconds or 0))))
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


def format_file_size(num_bytes: int | float) -> str:
    b = max(0.0, float(num_bytes or 0))
    if b < 1024:
        return f"{int(round(b))} B"
    units = ["KB", "MB", "GB"]
    v = b
    u = -1
    while True:
        v /= 1024.0
        u += 1
        if v < 1024.0 or u >= len(units) - 1:
            break
    return f"{v:.1f} {units[u]}"


def _draw_play_disc(d: int, accent: str) -> Image.Image:
    d = max(8, int(round(d)))
    ss = 2
    sd = d * ss
    im = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.ellipse([0, 0, sd - 1, sd - 1], fill=rgba_tuple(accent, 1.0))
    r = sd * 0.22
    cx = sd * 0.54
    cy = sd / 2.0
    dr.polygon([(cx - r * 0.7, cy - r), (cx - r * 0.7, cy + r), (cx + r * 1.1, cy)], fill=(255, 255, 255, 255))
    return im.resize((d, d), Image.LANCZOS)


def _draw_file_disc(d: int, accent: str) -> Image.Image:
    d = max(8, int(round(d)))
    ss = 2
    sd = d * ss
    im = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    acc_rgba = rgba_tuple(accent, 1.0)
    dr.ellipse([0, 0, sd - 1, sd - 1], fill=acc_rgba)
    w = sd * 0.34
    h = sd * 0.44
    x = (sd - w) / 2.0
    y = (sd - h) / 2.0
    fold = w * 0.38
    dr.polygon([(x, y), (x + w - fold, y), (x + w, y + fold), (x + w, y + h), (x, y + h)], fill=(255, 255, 255, 255))
    dr.polygon([(x + w - fold, y), (x + w - fold, y + fold), (x + w, y + fold)], fill=acc_rgba)
    return im.resize((d, d), Image.LANCZOS)


def _draw_note_disc(d: int, accent: str) -> Image.Image:
    d = max(8, int(round(d)))
    ss = 2
    sd = d * ss
    im = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.ellipse([0, 0, sd - 1, sd - 1], fill=rgba_tuple(accent, 1.0))
    head_r = sd * 0.09
    hx, hy = sd * 0.42, sd * 0.66
    stem_h = sd * 0.34
    stem_w = max(2, sd * 0.045)
    dr.ellipse([hx - head_r * 1.25, hy - head_r, hx + head_r * 1.25, hy + head_r], fill=(255, 255, 255, 255))
    dr.rectangle([hx + head_r * 1.1 - stem_w, hy - stem_h, hx + head_r * 1.1, hy], fill=(255, 255, 255, 255))
    dr.polygon([
        (hx + head_r * 1.1, hy - stem_h),
        (sd * 0.60, hy - stem_h + sd * 0.09),
        (sd * 0.58, hy - stem_h + sd * 0.18),
        (hx + head_r * 1.1, hy - stem_h + sd * 0.10),
    ], fill=(255, 255, 255, 255))
    return im.resize((d, d), Image.LANCZOS)


def _synthetic_waveform(duration: float, n: int) -> list[int]:
    a = ((int(round(float(duration or 0))) * 2654435761) + 0x9E3779B9) & 0xFFFFFFFF

    def rnd():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = (t ^ (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF))) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    p1 = rnd() * math.pi * 2.0
    p2 = rnd() * math.pi * 2.0
    f1 = 2.0 + rnd() * 2.0
    f2 = 9.0 + rnd() * 5.0
    out = []
    for i in range(n):
        x = (i / (n - 1)) if n > 1 else 0.0
        env = 0.55 + 0.35 * math.sin(x * math.pi * f1 + p1)
        rip = 0.5 + 0.5 * math.sin(i * f2 / 4.0 + p2)
        v = env * (0.3 + 0.7 * rip) * 1.15 + (rnd() - 0.5) * 0.4
        if math.sin(x * math.pi * (f1 * 2.3) + p2) > 0.93:
            v *= 0.25
        out.append(int(round(min(1.0, max(0.08, v)) * 31)))
    return out


def _resample_waveform(data: list | None, n: int) -> list[float]:
    clean = [min(31.0, max(0.0, float(v))) for v in (data or []) if isinstance(v, (int, float))]
    if not clean:
        return [0.0] * n
    if len(clean) <= n:
        return [v / 31.0 for v in clean]
    out = []
    for i in range(n):
        frm = int(math.floor(i * len(clean) / n))
        to = max(frm + 1, int(math.floor((i + 1) * len(clean) / n)))
        out.append(sum(clean[frm:to]) / (to - frm) / 31.0)
    return out


def draw_voice_row(waveform: list | None, duration: float, accent: str, text_color: str, scale: int, max_width: float) -> QCanvas:
    P = GLASS_STYLE
    s = lambda v: v * scale
    d = int(round(s(P["row_disc"])))
    dur_label = draw_label(format_duration(duration), int(round(s(P["font_micro"]))), text_color, alpha=P["micro_alpha"])

    pitch = s(P["row_bar"]) + s(P["row_bar_gap"])
    bars_avail = max(pitch * 8, (max_width or s(220)) - d - s(P["row_gap"]) * 2 - dur_label.width)
    fit = max(8, int(math.floor(bars_avail / pitch)))

    has_wf = isinstance(waveform, list) and any(isinstance(v, (int, float)) and v > 0 for v in waveform)
    if has_wf:
        bar_count = min(max(8, len(waveform)), fit)
        heights = _resample_waveform(waveform, bar_count)
    else:
        bar_count = min(fit, max(16, int(round(float(duration or 0) * 2))))
        heights = _resample_waveform(_synthetic_waveform(duration, bar_count), bar_count)

    bars_w = bar_count * pitch - s(P["row_bar_gap"])
    w = int(math.ceil(d + s(P["row_gap"]) + bars_w + s(P["row_gap"]) + dur_label.width))
    h = d
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.alpha_composite(_draw_play_disc(d, accent), (0, 0))

    dr = ImageDraw.Draw(canvas)
    acc_rgba = rgba_tuple(accent, 1.0)
    cy = h / 2.0
    bar_w = s(P["row_bar"])
    for i in range(bar_count):
        bh = max(s(P["row_bar_min"]), heights[i] * s(P["row_bar_max"]))
        bx = d + s(P["row_gap"]) + i * pitch
        by = cy - bh / 2.0
        dr.rounded_rectangle([bx, by, bx + bar_w, by + bh], radius=bar_w / 2.0, fill=acc_rgba)

    o = dur_label.optical
    ly = int(round(h / 2.0 - (o["t"] + dur_label.height - o["b"]) / 2.0))
    canvas.alpha_composite(dur_label.image, (int(round(d + s(P["row_gap"]) + bars_w + s(P["row_gap"]))), ly))
    return QCanvas(canvas, 0, 0)


def _fit_label(text: str, font_size: int, bold: bool, max_w: float, mode: str = "end") -> str:
    if not max_w or max_w <= 0:
        return text
    f = _load_style_font(font_size, ("bold",) if bold else ())
    if _measure_text_width(text, f) <= max_w:
        return text
    chars = _grapheme_clusters(text)
    ell = "…"
    if mode == "end":
        n = len(chars)
        while n > 1 and _measure_text_width("".join(chars[:n]).rstrip() + ell, f) > max_w:
            n -= 1
        return "".join(chars[:n]).rstrip() + ell

    dot = text.rfind(".")
    ext_len = len(_grapheme_clusters(text[dot:])) if (dot > 0 and len(text) - dot <= 8) else 0
    stem = len(chars) - ext_len
    for tail in range(min(ext_len + 4, len(chars) - 2), ext_len - 1, -1):
        tail_str = "".join(chars[len(chars) - tail :])
        head = min(len(chars) - tail, stem) - 1
        while head > 0 and _measure_text_width("".join(chars[:head]) + ell + tail_str, f) > max_w:
            head -= 1
        if head >= 3:
            return "".join(chars[:head]) + ell + tail_str
    return _fit_label(text, font_size, bold, max_w, "end")


def _assemble_row(disc: Image.Image, title: QCanvas, meta: QCanvas | None, scale: int, max_width: float) -> QCanvas:
    P = GLASS_STYLE
    s = lambda v: v * scale
    gap = s(P["row_gap"])
    text_w = max(title.width, meta.width if meta else 0)
    w = int(math.ceil(disc.width + gap + text_w))
    if max_width and w > max_width:
        w = int(math.ceil(max_width))
    vis = lambda c: c.height - c.optical["t"] - c.optical["b"]
    line_gap = s(P["row_line_gap"])
    texts_h = vis(title) + (line_gap + vis(meta) if meta else 0)
    h = int(math.ceil(max(disc.height, texts_h)))
    pad_t = title.optical["t"]
    pad_b = (meta or title).optical["b"]
    h_all = int(math.ceil(pad_t + h + pad_b))

    canvas = Image.new("RGBA", (w, h_all), (0, 0, 0, 0))
    canvas.alpha_composite(disc, (0, int(round(pad_t + (h - disc.height) / 2.0))))
    ty = int(round(pad_t + (h - texts_h) / 2.0))
    tx = int(round(disc.width + gap))
    canvas.alpha_composite(title.image, (tx, int(round(ty - title.optical["t"]))))
    if meta:
        canvas.alpha_composite(meta.image, (tx, int(round(ty + vis(title) + line_gap - meta.optical["t"]))))
    return QCanvas(canvas, pad_t, h_all - pad_t - h)


def draw_document_row(doc: dict, accent: str, text_color: str, scale: int, max_width: float) -> QCanvas:
    P = GLASS_STYLE
    s = lambda v: int(round(v * scale))
    d = s(P["row_disc"])
    name = str(doc.get("file_name") or "File")
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else ""
    meta_parts = [format_file_size(doc["file_size"]) if doc.get("file_size") is not None else None, ext or None]
    meta_text = " · ".join(p for p in meta_parts if p)
    text_max = (max_width - d - s(P["row_gap"])) if max_width else 0
    title = draw_label(_fit_label(name, s(P["row_title"]), True, text_max, "middle"), s(P["row_title"]), text_color, bold=True)
    meta = draw_label(meta_text, s(P["font_micro"]), text_color, alpha=P["micro_alpha"]) if meta_text else None
    return _assemble_row(_draw_file_disc(d, accent), title, meta, scale, max_width)


def draw_audio_row(audio: dict, accent: str, text_color: str, scale: int, max_width: float, thumb_bytes: bytes | None = None) -> QCanvas:
    P = GLASS_STYLE
    s = lambda v: int(round(v * scale))
    d = s(P["row_disc"])
    lead = None
    if thumb_bytes:
        try:
            t_img = Image.open(io.BytesIO(thumb_bytes)).convert("RGBA")
            sq = cover_square(t_img).resize((d, d), Image.LANCZOS)
            lead = round_image(sq, s(P["row_cover"]))
        except Exception:
            lead = None
    if lead is None:
        lead = _draw_note_disc(d, accent)

    meta_parts = [audio.get("performer") or None, format_duration(audio["duration"]) if audio.get("duration") is not None else None]
    meta_text = " · ".join(str(p) for p in meta_parts if p)
    text_max = (max_width - d - s(P["row_gap"])) if max_width else 0
    a_title = _fit_label(str(audio.get("title") or "Audio"), s(P["row_title"]), True, text_max, "end")
    title = draw_label(a_title, s(P["row_title"]), text_color, bold=True)
    meta = draw_label(meta_text, s(P["font_micro"]), text_color, alpha=P["micro_alpha"]) if meta_text else None
    return _assemble_row(lead, title, meta, scale, max_width)


def paint_media_badges(canvas: Image.Image, x: int, y: int, w: int, h: int, badge: dict | None, scale: int):
    if not badge:
        return
    P = GLASS_STYLE
    s = lambda v: v * scale
    if badge.get("play"):
        d = int(round(min(s(P["badge_play"]), w * 0.45, h * 0.45)))
        ss = 2
        sd = d * ss
        disc = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
        dr = ImageDraw.Draw(disc)
        dr.ellipse([0, 0, sd - 1, sd - 1], fill=(0, 0, 0, int(round(P["badge_bg"] * 255))))
        r = sd * 0.24
        cx, cy = sd / 2.0, sd / 2.0
        dr.polygon([(cx - r * 0.62, cy - r), (cx - r * 0.62, cy + r), (cx + r * 1.05, cy)], fill=(255, 255, 255, 255))
        disc = disc.resize((d, d), Image.LANCZOS)
        canvas.alpha_composite(disc, (int(round(x + (w - d) / 2.0)), int(round(y + (h - d) / 2.0))))

    if badge.get("label"):
        lbl = draw_label(str(badge["label"]), int(round(s(P["font_micro"]))), "#ffffff")
        o = lbl.optical
        pad_x = s(P["badge_pad_x"])
        pad_y = s(P["badge_pad_y"])
        bw = int(math.ceil(lbl.width + pad_x * 2))
        bh = int(math.ceil(lbl.height - o["t"] - o["b"] + pad_y * 2))
        bx = int(round(x + s(P["badge_inset"])))
        by = int(round(y + h - bh - s(P["badge_inset"])))
        pill = draw_bubble("#000000", "#000000", bw, bh, bh / 2.0, 0, 0).image
        a = pill.split()[3].point(lambda p: int(round(p * P["badge_bg"])))
        pill.putalpha(a)
        pill.alpha_composite(lbl.image, (int(round(pad_x)), int(round(pad_y - o["t"]))))
        canvas.alpha_composite(pill, (bx, by))


def apply_spoiler(img: Image.Image, scale: int = 2) -> Image.Image:
    max_side = 800
    blur_side = 9
    k = min(1.0, max_side / max(1, img.width, img.height))
    w = max(2, int(round(img.width * k)))
    h = max(2, int(round(img.height * k)))

    src = Image.new("RGBA", (w, h), (128, 128, 128, 255))
    src.alpha_composite(img.convert("RGBA").resize((w, h), Image.LANCZOS))

    tiny_w = max(2, int(round(blur_side if w >= h else (blur_side * w) / h)))
    tiny_h = max(2, int(round(blur_side if h > w else (blur_side * h) / w)))
    tiny = src.resize((tiny_w, tiny_h), Image.BICUBIC)
    blurred = tiny.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(8))

    dim = Image.new("RGBA", (w, h), (30, 34, 44, int(round(0.28 * 255))))
    out = Image.alpha_composite(blurred, dim)

    seed = ((w * 73856093) ^ (h * 19349663)) & 0xFFFFFFFF

    def rnd():
        nonlocal seed
        seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
        return seed / 4294967296.0

    dot = max(1.0, (scale * w) / 640.0 * 1.1)
    count = int(round((w * h) / (dot * dot * 22.0)))
    dr = ImageDraw.Draw(out)
    for _ in range(count):
        px = rnd() * w
        py = rnd() * h
        pr = dot * (0.45 + rnd() * 0.75)
        alpha = int(round((0.35 + rnd() * 0.5) * 255))
        dr.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(255, 255, 255, alpha))
    return out


# ---- Text Engine: text-prepare.js + text-layout.js + text-render.js --------

def _utf16_to_char_map(text: str) -> list[int]:
    mapping = []
    for idx, ch in enumerate(text):
        mapping.append(idx)
        if ord(ch) > 0xFFFF:
            mapping.append(idx)
    mapping.append(len(text))
    return mapping


def split_by_blockquotes(text: str, entities: list[dict] | None) -> list[dict] | None:
    if not isinstance(entities, list) or not entities:
        return None
    quotes = [
        e for e in entities
        if isinstance(e, dict) and e.get("type") in ("blockquote", "expandable_blockquote")
    ]
    if not quotes:
        return None
    quotes.sort(key=lambda e: e.get("offset", 0))
    u_map = _utf16_to_char_map(text)
    max_u = len(u_map) - 1

    def slice_entities(u_start: int, u_end: int):
        out = []
        for e in entities:
            if not isinstance(e, dict) or e.get("type") in ("blockquote", "expandable_blockquote"):
                continue
            eo = int(e.get("offset", 0))
            el = int(e.get("length", 0))
            if eo < u_end and eo + el > u_start:
                frm = max(eo, u_start)
                to = min(eo + el, u_end)
                out.append({**e, "offset": frm - u_start, "length": to - frm})
        return out

    parts = []
    pos = 0
    for q in quotes:
        qo = max(0, min(max_u, int(q.get("offset", 0))))
        ql = max(0, min(max_u - qo, int(q.get("length", 0))))
        if qo < pos:
            continue
        if qo > pos:
            plain = text[u_map[pos] : u_map[qo]].rstrip("\n")
            if plain:
                parts.append({"text": plain, "entities": slice_entities(pos, qo), "quote": False})
        parts.append({"text": text[u_map[qo] : u_map[qo + ql]], "entities": slice_entities(qo, qo + ql), "quote": True})
        pos = qo + ql

    if pos < max_u:
        tail = text[u_map[pos] :].lstrip("\n")
        if tail:
            parts.append({"text": tail, "entities": slice_entities(pos, max_u), "quote": False})
    return parts if parts else None


def prepare_text(text: str, entities: list[dict] | str | None, font_size: int, custom_emojis: dict | None = None) -> dict:
    font_size = int(round(font_size))
    asc, desc = font_metrics(font_size)
    line_height = font_size * 1.2
    emoji_size = font_size * EMOJI_SCALE

    if not text:
        return {
            "segments": [], "fontSize": font_size, "lineHeight": line_height,
            "emojiSize": emoji_size, "ascent": asc, "descent": desc,
        }

    text = text.replace("і", "i")
    n = len(text)
    char_styles = [[] for _ in range(n)]
    char_custom_emoji = [None] * n

    if isinstance(entities, str):
        for s in char_styles:
            s.append(entities)
    elif isinstance(entities, list):
        u_map = _utf16_to_char_map(text)
        max_u = len(u_map) - 1
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            etype = ent.get("type", "")
            st = (
                "monospace" if etype in ENTITY_TYPES_MONOSPACE
                else "mention" if etype in ENTITY_TYPES_MENTION
                else etype
            )
            o = max(0, min(max_u, int(ent.get("offset", 0))))
            l = max(0, min(max_u - o, int(ent.get("length", 0))))
            c_start, c_end = u_map[o], u_map[o + l]
            if etype == "custom_emoji" and ent.get("custom_emoji_id") and c_start < n:
                cid = str(ent["custom_emoji_id"])
                for i in range(c_start, c_end):
                    char_custom_emoji[i] = cid
            for i in range(c_start, c_end):
                if st not in char_styles[i]:
                    char_styles[i].append(st)

    cleaned_chars = [qe.clean(c) for c in text]
    clean_text = "".join(cleaned_chars)
    while len(char_styles) < len(clean_text):
        char_styles.append(list(char_styles[-1]) if char_styles else [])
        char_custom_emoji.append(None)

    emoji_at = [None] * len(clean_text)
    for e_idx, m in enumerate(qe.EMOJI_RE.finditer(clean_text)):
        code = m.group()
        for i in range(m.start(), m.end()):
            if i < len(emoji_at):
                emoji_at[i] = (e_idx, code, char_custom_emoji[i])

    for i, cid in enumerate(char_custom_emoji):
        if cid and emoji_at[i] is None:
            emoji_at[i] = (10000 + i, clean_text[i], cid)

    segments = []
    for m in re.finditer(r"\r\n|\r|\n|[^\S\r\n]+|\w+|[^\w\s]", clean_text):
        start, end = m.start(), m.end()
        sub_start = start
        for i in range(start, end):
            ch = clean_text[i]
            prev_i = i - 1 if i > sub_start else None
            is_break = ch in ("\r", "\n")
            needs_split = is_break or (
                prev_i is not None and (
                    emoji_at[i] != emoji_at[prev_i]
                    or char_styles[i] != char_styles[prev_i]
                )
            )
            if needs_split:
                if i > sub_start:
                    _push_segment(segments, clean_text, char_styles, emoji_at, sub_start, i, font_size, emoji_size, custom_emojis)
                sub_start = i
        if sub_start < end:
            _push_segment(segments, clean_text, char_styles, emoji_at, sub_start, end, font_size, emoji_size, custom_emojis)

    cjk_segs = []
    for seg in segments:
        if seg["kind"] != "text" or not CJK_RE.search(seg["text"]):
            cjk_segs.append(seg)
            continue
        clusters = _grapheme_clusters(seg["text"])
        if len(clusters) <= 1:
            cjk_segs.append(seg)
            continue
        for cl in clusters:
            cjk_segs.append({
                **seg,
                "text": cl,
                "width": _measure_text_width(cl, seg["font"]),
            })

    merged = []
    for seg in cjk_segs:
        if (
            seg["kind"] == "text"
            and len(seg["text"]) == 1
            and (seg["text"] in LEFT_STICKY_PUNCTUATION or seg["text"] in KINSOKU_START)
            and merged
            and merged[-1]["kind"] == "text"
            and merged[-1]["styles"] == seg["styles"]
        ):
            merged[-1]["text"] += seg["text"]
            merged[-1]["width"] = _measure_text_width(merged[-1]["text"], merged[-1]["font"])
            continue
        merged.append(seg)

    return {
        "segments": merged,
        "fontSize": font_size,
        "lineHeight": line_height,
        "emojiSize": emoji_size,
        "ascent": asc,
        "descent": desc,
    }


def _push_segment(out: list, text: str, char_styles: list, emoji_at: list, start: int, end: int, font_size: int, emoji_size: float, custom_emojis: dict | None):
    sub = text[start:end]
    styles = list(char_styles[start])
    em = emoji_at[start]

    if sub in ("\r", "\n", "\r\n"):
        kind = "break"
    elif sub.isspace():
        kind = "space"
    elif em is not None:
        kind = "emoji"
    else:
        kind = "text"

    f = _load_style_font(font_size, styles)
    em_img = None
    if kind == "emoji":
        cid = em[2]
        if cid and isinstance(custom_emojis, dict) and cid in custom_emojis:
            raw_ce = custom_emojis[cid]
            if isinstance(raw_ce, Image.Image):
                em_img = raw_ce
            elif isinstance(raw_ce, (bytes, bytearray)):
                try:
                    em_img = Image.open(io.BytesIO(raw_ce)).convert("RGBA")
                except Exception:
                    em_img = None
        if em_img is None:
            em_img = qe.fetch(em[1])

    w = emoji_size if kind == "emoji" else (0.0 if kind == "break" else _measure_text_width(sub, f))

    out.append({
        "text": sub,
        "kind": kind,
        "styles": styles,
        "font": f,
        "emojiImage": em_img,
        "width": w,
    })


def _get_line_direction(segments: list, start_index: int) -> str:
    for i in range(start_index, len(segments)):
        seg = segments[i]
        if seg["kind"] in ("space", "break"):
            continue
        if RTL_RE.search(seg["text"]):
            return "rtl"
        return "ltr"
    return "ltr"


def _apply_truncation(line: dict, prepared: dict, max_width: float):
    if not line["segments"]:
        line["width"] = 0
        line["truncated"] = True
        return
    segments = prepared["segments"]
    last_seg = segments[line["segments"][-1]["index"]]
    ell_w = _measure_text_width("…", last_seg["font"])
    target_w = max_width - ell_w

    total_w = 0.0
    trim_idx = len(line["segments"])
    for idx, s in enumerate(line["segments"]):
        if total_w + s["width"] > target_w:
            trim_idx = idx
            break
        total_w += s["width"]

    line["segments"] = line["segments"][:max(1, trim_idx)]
    while len(line["segments"]) > 1 and segments[line["segments"][-1]["index"]]["kind"] == "space":
        total_w -= line["segments"][-1]["width"]
        line["segments"].pop()
    if total_w <= 0:
        total_w = line["segments"][0]["width"]
    line["width"] = total_w + ell_w
    line["truncated"] = True


def layout_text(prepared: dict, max_width: float, max_height: float) -> dict:
    segments = prepared["segments"]
    line_height = prepared["lineHeight"]
    emoji_size = prepared["emojiSize"]
    ascent = prepared["ascent"]
    descent = prepared["descent"]

    if not segments:
        return {"lines": [], "width": 0, "height": 0, "lineCount": 0, "truncated": False}

    max_width = min(10000.0, max(1.0, float(max_width)))
    max_height = min(10000.0, float(max_height)) if max_height > 0 else 0.0

    lines = []
    cur = {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, 0), "truncated": False}
    line_y = float(ascent)
    max_line_w = 0.0
    truncated = False
    pending_break = None

    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg["kind"] == "break":
            has_more = (i + 1) < len(segments)
            if has_more and max_height > 0 and (line_y + line_height > max_height):
                truncated = True
                _apply_truncation(cur, prepared, max_width)
                break
            if cur["width"] > max_line_w:
                max_line_w = cur["width"]
            cur["y"] = line_y
            lines.append(cur)
            line_y += line_height
            pending_break = None
            cur = {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, i + 1), "truncated": False}
            i += 1
            continue

        seg_w = emoji_size if seg["kind"] == "emoji" else seg["width"]

        if seg["kind"] == "space" and cur["width"] + seg_w > max_width:
            cur["segments"].append({"index": i, "width": seg_w})
            i += 1
            continue

        if not cur["segments"] and seg_w > max_width and seg["kind"] == "text" and len(seg["text"]) > 1:
            clusters = _grapheme_clusters(seg["text"])
            if len(clusters) > 1:
                c_widths = [_measure_text_width(c, seg["font"]) for c in clusters]
                cursor = 0
                while cursor < len(clusters):
                    fit_cnt, fit_w = 0, 0.0
                    for g in range(cursor, len(clusters)):
                        if fit_w + c_widths[g] > max_width and fit_cnt > 0:
                            break
                        fit_w += c_widths[g]
                        fit_cnt += 1
                    if fit_cnt == 0:
                        fit_cnt = 1
                        fit_w = c_widths[cursor]
                    is_last = (cursor + fit_cnt) >= len(clusters)
                    sub_str = "".join(clusters[cursor : cursor + fit_cnt])
                    cur["segments"].append({"index": i, "width": fit_w, "sub_text": sub_str})
                    cur["width"] += fit_w
                    if not is_last:
                        if max_height > 0 and (line_y + line_height > max_height):
                            truncated = True
                            _apply_truncation(cur, prepared, max_width)
                            break
                        if cur["width"] > max_line_w:
                            max_line_w = cur["width"]
                        cur["y"] = line_y
                        lines.append(cur)
                        line_y += line_height
                        cur = {"segments": [], "width": 0.0, "direction": cur["direction"], "truncated": False}
                    cursor += fit_cnt
                if truncated:
                    break
                i += 1
                continue

        if cur["width"] + seg_w > max_width and cur["segments"]:
            if max_height > 0 and (line_y + line_height > max_height):
                truncated = True
                _apply_truncation(cur, prepared, max_width)
                break
            if pending_break is not None:
                b_idx, b_w, b_cnt = pending_break
                cur["segments"] = cur["segments"][:b_cnt]
                cur["width"] = b_w
                if cur["width"] > max_line_w:
                    max_line_w = cur["width"]
                cur["y"] = line_y
                lines.append(cur)
                line_y += line_height
                pending_break = None
                cur = {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, b_idx + 1), "truncated": False}
                i = b_idx + 1
                continue

            if cur["width"] > max_line_w:
                max_line_w = cur["width"]
            cur["y"] = line_y
            lines.append(cur)
            line_y += line_height
            cur = {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, i), "truncated": False}
            if seg["kind"] != "space":
                cur["segments"].append({"index": i, "width": seg_w})
                cur["width"] += seg_w
            pending_break = None
            i += 1
            continue

        if seg["kind"] == "space":
            pending_break = (i, cur["width"], len(cur["segments"]))

        cur["segments"].append({"index": i, "width": seg_w})
        cur["width"] += seg_w
        i += 1

    if cur["segments"]:
        if cur["width"] > max_line_w:
            max_line_w = cur["width"]
        cur["y"] = line_y
        lines.append(cur)

    for line in lines:
        x = 0.0
        for lseg in line["segments"]:
            lseg["x"] = x
            x += lseg["width"]

    total_h = (lines[-1]["y"] + descent) if lines else 0
    return {"lines": lines, "width": max_line_w, "height": total_h, "lineCount": len(lines), "truncated": truncated}


def shrink_wrap(prepared: dict, max_width: float, max_height: float) -> dict:
    initial = layout_text(prepared, max_width, max_height)
    if initial["lineCount"] <= 1 or initial["truncated"]:
        return initial

    max_seg_w = max((s["width"] for s in prepared["segments"]), default=0.0)
    lo = max(max_seg_w, initial["width"] / initial["lineCount"] * 0.7)
    hi = initial["width"]
    target = initial["lineCount"]

    while hi - lo > 2.0:
        mid = (lo + hi) / 2.0
        trial = layout_text(prepared, mid, max_height)
        if trial["lineCount"] <= target and not trial["truncated"]:
            hi = mid
        else:
            lo = mid

    return layout_text(prepared, math.ceil(hi), max_height)


def render_text(layout_res: dict, prepared: dict, font_color: str) -> QCanvas:
    lines = layout_res["lines"]
    width = layout_res["width"]
    height = layout_res["height"]
    segments = prepared["segments"]
    font_size = prepared["fontSize"]
    emoji_size = int(round(prepared["emojiSize"]))

    if not lines or width <= 0:
        return QCanvas(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)

    cw = max(1, int(math.ceil(width)))
    ch = max(1, int(math.ceil(height)))
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    dr = ImageDraw.Draw(canvas)

    for line_idx, line in enumerate(lines):
        for lseg in line["segments"]:
            seg = segments[lseg["index"]]
            st = seg["styles"]
            if "monospace" in st:
                fill = rgba_tuple("#5887a7", 1.0)
            elif "mention" in st:
                fill = rgba_tuple("#6ab7ec", 1.0)
            elif "spoiler" in st:
                fill = rgba_tuple(font_color, 0.15)
            else:
                fill = rgba_tuple(font_color, 1.0)

            draw_x = lseg["x"]
            if line["direction"] == "rtl":
                sw = emoji_size if seg["kind"] == "emoji" else lseg["width"]
                draw_x = width - lseg["x"] - sw
            draw_y = line["y"]

            if seg["kind"] == "emoji":
                if seg["emojiImage"] is not None:
                    em = seg["emojiImage"].resize((emoji_size, emoji_size), Image.LANCZOS)
                    ey = int(round(draw_y - font_size + (font_size * 0.15)))
                    canvas.alpha_composite(em, (int(round(draw_x)), ey))
                else:
                    _draw_text_baseline(dr, draw_x, draw_y, seg["text"], seg["font"], fill)
            elif seg["kind"] in ("space", "break"):
                pass
            else:
                draw_str = lseg.get("sub_text", seg["text"])
                _draw_text_baseline(dr, draw_x, draw_y, draw_str, seg["font"], fill)
                tw_val = _measure_text_width(draw_str, seg["font"])
                if "strikethrough" in st:
                    sy = draw_y - font_size / 2.8
                    dr.rectangle([draw_x, sy, draw_x + tw_val, sy + font_size * 0.1], fill=fill)
                if "underline" in st:
                    uy = draw_y + 2
                    dr.rectangle([draw_x, uy, draw_x + tw_val, uy + font_size * 0.1], fill=fill)

        if line_idx == len(lines) - 1 and line.get("truncated"):
            last_lseg = line["segments"][-1] if line["segments"] else None
            if last_lseg:
                last_seg = segments[last_lseg["index"]]
                ell_x = last_lseg["x"] + last_lseg["width"]
                if line["direction"] == "rtl":
                    ell_x = width - last_lseg["x"] - last_lseg["width"] - _measure_text_width("…", last_seg["font"])
                _draw_text_baseline(dr, ell_x, line["y"], "…", last_seg["font"], rgba_tuple(font_color, 1.0))

    first_line = lines[0]
    last_line = lines[-1]
    any_bold = any(s["kind"] == "text" and "bold" in s["styles"] for s in segments)
    first_items = [segments[ls["index"]] for ls in first_line["segments"] if segments[ls["index"]]["kind"] not in ("space", "break")]
    if first_items and all(s["kind"] == "emoji" for s in first_items):
        top_val = font_size * 0.85
    else:
        top_val = float(cap_height(font_size, any_bold))
        for s in first_items:
            if s["kind"] == "text" and TALL_SCRIPT_RE.search(s["text"]):
                for sub_s, g_font in _runs_with_fallback(s["text"], s["font"]):
                    try:
                        bbox = _DUMMY_DR.textbbox((0, 0), sub_s, font=g_font, anchor="ls")
                        top_val = max(top_val, float(abs(bbox[1])))
                    except Exception:
                        pass

    last_items = [segments[ls["index"]] for ls in last_line["segments"]]
    emoji_bottom = (emoji_size - font_size * 0.85) if last_items and all(s["kind"] == "emoji" for s in last_items) else 0.0
    return QCanvas(canvas, first_line["y"] - top_val, math.ceil(height) - last_line["y"] - emoji_bottom)


def draw_multiline_text(
    text: str,
    entities: list[dict] | str | None,
    font_size: int,
    font_color: str,
    max_width: float,
    max_height: float,
    custom_emojis: dict | None = None,
) -> QCanvas:
    prepared = prepare_text(text, entities, font_size, custom_emojis=custom_emojis)
    if not prepared["segments"]:
        return QCanvas(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)
    lay = shrink_wrap(prepared, max_width, max_height)
    return render_text(lay, prepared, font_color)


# ---- Box-Model Layout & Accent Block (layout-box.js & composer.js) ---------

def _norm_pad(pad) -> dict:
    if not pad:
        return {"t": 0, "r": 0, "b": 0, "l": 0}
    if isinstance(pad, (int, float)):
        return {"t": pad, "r": pad, "b": pad, "l": pad}
    return {"t": pad.get("t", 0), "r": pad.get("r", 0), "b": pad.get("b", 0), "l": pad.get("l", 0)}


def leaf(qc: QCanvas | Image.Image | None, **opts) -> dict | None:
    if qc is None:
        return None
    if isinstance(qc, Image.Image):
        qc = QCanvas(qc, 0, 0)
    if qc.width <= 1 and qc.height <= 1:
        return None
    w = opts.get("w", qc.width)
    if opts.get("maxW") and w > opts["maxW"]:
        w = opts["maxW"]
    use_opt = opts.get("optical", True) and opts.get("trim", True) and ("h" not in opts)
    trim_t = qc.optical["t"] if use_opt else 0
    trim_b = qc.optical["b"] if use_opt else 0
    raw_h = opts.get("h", qc.height)
    return {
        "kind": "leaf", "qc": qc, "trimT": trim_t,
        "role": opts.get("role", "block"),
        "w": w, "h": raw_h - trim_t - trim_b,
        "bleed": bool(opts.get("bleed", False)),
        "paint": opts.get("paint"),
    }


def box(**opts) -> dict:
    return {
        "kind": "box",
        "dir": opts.get("dir", "col"),
        "gap": opts.get("gap", 0),
        "role": opts.get("role", "block"),
        "pad": _norm_pad(opts.get("pad")),
        "align": opts.get("align", "start"),
        "justify": opts.get("justify", "start"),
        "stretch": bool(opts.get("stretch", False)),
        "bg": opts.get("bg"),
        "fg": opts.get("fg"),
        "minW": opts.get("minW", 0),
        "maxW": opts.get("maxW", 0),
        "children": [c for c in (opts.get("children") or []) if c is not None],
    }


def _gap_before(b_node: dict, child: dict, idx: int) -> float:
    if idx == 0:
        return 0
    if "mt" in child:
        return child["mt"]
    g = b_node["gap"]
    return g(b_node["children"][idx - 1], child) if callable(g) else g


def measure(n: dict) -> dict:
    if n["kind"] == "leaf":
        return n
    for c in n["children"]:
        measure(c)
    w, h = 0.0, 0.0
    p = n["pad"]
    if n["dir"] == "col":
        outer_w = 0.0
        for i, c in enumerate(n["children"]):
            cw = c["w"] if c.get("bleed") else (c["w"] + p["l"] + p["r"])
            if cw > outer_w:
                outer_w = cw
            h += _gap_before(n, c, i) + c["h"]
        w = outer_w
    else:
        for c in n["children"]:
            if c["h"] > h:
                h = c["h"]
            w += c["w"]
        w += (n["gap"] if not callable(n["gap"]) else 0) * max(0, len(n["children"]) - 1)
        w += p["l"] + p["r"]
    n["w"] = max(int(math.ceil(w)), int(math.ceil(n["minW"])))
    n["h"] = int(math.ceil(h + p["t"] + p["b"]))
    if n["maxW"] and n["w"] > n["maxW"]:
        n["w"] = int(math.ceil(n["maxW"]))
    return n


def place(n: dict, x: float, y: float, stretch_w: float | None = None):
    if stretch_w is not None and n.get("stretch"):
        n["w"] = int(round(stretch_w))
    n["x"] = int(round(x))
    n["y"] = int(round(y))
    x, y = n["x"], n["y"]
    if n["kind"] == "leaf":
        return
    p = n["pad"]
    inner_w = n["w"] - p["l"] - p["r"]
    if n["dir"] == "col":
        cy = y + p["t"]
        for i, c in enumerate(n["children"]):
            cx = x + p["l"]
            if c.get("bleed"):
                cx = x + max(0.0, (n["w"] - c["w"]) / 2.0)
            elif n["align"] == "center" and c["w"] < inner_w:
                cx += (inner_w - c["w"]) / 2.0
            cy += _gap_before(n, c, i)
            place(c, cx, cy, inner_w)
            cy += c["h"]
    else:
        def cross_y(c):
            if n["align"] == "center":
                return y + p["t"] + (n["h"] - p["t"] - p["b"] - c["h"]) / 2.0
            if n["align"] == "end":
                return y + n["h"] - p["b"] - c["h"]
            return y + p["t"]

        gap_val = n["gap"] if not callable(n["gap"]) else 0
        if n["justify"] == "between" and len(n["children"]) == 2:
            a, b = n["children"]
            avail_a = inner_w - b["w"] - gap_val
            if a["w"] > avail_a:
                a["w"] = max(0, int(round(avail_a)))
            place(a, x + p["l"], cross_y(a))
            place(b, x + n["w"] - p["r"] - b["w"], cross_y(b))
            return
        cx = x + p["l"]
        for c in n["children"]:
            place(c, cx, cross_y(c))
            cx += c["w"] + gap_val


def _fade_overflow(leaf_node: dict) -> Image.Image:
    im = leaf_node["qc"].image
    w = max(1, int(round(leaf_node["w"])))
    h = im.height
    alpha = im.split()[3].load()
    cut = min(w, im.width)
    for x in range(cut - 1, int(cut * 0.4), -1):
        if not any(alpha[x, y] > 8 for y in range(h)):
            cut = x
            break
    return im.crop((0, 0, max(1, cut), h))


def render_tree(canvas: Image.Image, n: dict):
    if n.get("bg"):
        n["bg"](canvas, n)
    if n["kind"] == "leaf":
        if n.get("paint"):
            n["paint"](canvas, n)
        else:
            im = n["qc"].image
            if im.width > n["w"] + 1:
                im = _fade_overflow(n)
            canvas.alpha_composite(im, (int(round(n["x"])), int(round(n["y"] - n["trimT"]))))
    else:
        for c in n["children"]:
            render_tree(canvas, c)
    if n.get("fg"):
        n["fg"](canvas, n)


def _paint_stripe(bar_img: Image.Image, colors: list[str], s_fn) -> Image.Image:
    w, h = bar_img.size
    dash = s_fn(GLASS_STYLE["stripe_dash"]) * (0.85 if len(colors) > 2 else 1.0)
    slant = s_fn(GLASS_STYLE["stripe_slant"])
    stripe_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(stripe_layer)
    y = -float(slant)
    i = 0
    while y < h + slant:
        col = rgba_tuple(colors[i % len(colors)], 1.0)
        dr.polygon([(0, y + slant), (w, y), (w, y + dash), (0, y + dash + slant)], fill=col)
        y += dash
        i += 1
    stripe_layer.putalpha(ImageChops.multiply(stripe_layer.split()[3], bar_img.split()[3]))
    return stripe_layer


def _paint_emoji_pattern(canvas: Image.Image, n: dict, emoji_img: Image.Image, accent: str, shape: Image.Image, s_fn):
    """Exact port of paintEmojiPattern(ctx, n, emoji, accent, shape, P, s) in composer.js."""
    bw, bh = int(math.ceil(n["w"])), int(math.ceil(n["h"]))
    clip = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    acc_rgb = hex_to_rgb(accent)
    bar_limit = bw - s_fn(GLASS_STYLE["block_bar"]) * 2
    for cell in EMOJI_CELLS:
        size = max(2, int(round(s_fn(cell["size"]))))
        if s_fn(cell["dx"]) + size > bar_limit:
            continue
        em_scaled = emoji_img.convert("RGBA").resize((size, size), Image.LANCZOS)
        # source-in tint with accent color
        tinted = Image.new("RGBA", (size, size), (*acc_rgb, 255))
        tinted.putalpha(em_scaled.split()[3])
        px = int(round(bw - s_fn(cell["dx"]) - size / 2.0))
        py = int(round(bh * cell["dy"] - size / 2.0))
        clip.alpha_composite(tinted, (px, py))

    # destination-in clip to rounded block shape + 0.14 alpha
    clipped_alpha = ImageChops.multiply(clip.split()[3], shape.split()[3])
    clipped_alpha = clipped_alpha.point(lambda p: int(round(p * GLASS_STYLE["emoji_alpha"])))
    clip.putalpha(clipped_alpha)
    canvas.alpha_composite(clip, (n["x"], n["y"]))


def accent_block(
    s_fn,
    accent: str,
    children: list,
    icon: bool = False,
    stripe: list[str] | None = None,
    pattern_emoji: Image.Image | None = None,
) -> dict:
    P = GLASS_STYLE
    pad_y = s_fn(P["block_pad_y"])
    pad_l = s_fn(P["block_pad_l"])
    pad_r = s_fn(P["block_pad_r_icon"] if icon else P["block_pad_r"])
    bar_w = int(math.ceil(s_fn(P["block_bar"])))
    radius = s_fn(P["block_radius"])

    def paint_bg(canvas: Image.Image, n: dict):
        bw, bh = int(math.ceil(n["w"])), int(math.ceil(n["h"]))
        solid = draw_bubble(accent, accent, bw, bh, radius, 0, 0).image
        tinted = solid.copy()
        a = tinted.split()[3].point(lambda p: int(round(p * P["block_tint"])))
        tinted.putalpha(a)
        canvas.alpha_composite(tinted, (n["x"], n["y"]))
        bar_img = solid.crop((0, 0, min(bar_w, bw), bh))
        if stripe and len(stripe) > 1:
            bar_img = _paint_stripe(bar_img, stripe, s_fn)
        canvas.alpha_composite(bar_img, (n["x"], n["y"]))
        if pattern_emoji is not None:
            _paint_emoji_pattern(canvas, n, pattern_emoji, accent, solid, s_fn)
        if icon:
            qi = draw_quote_icon(s_fn(P["block_icon"]), accent, 1.0)
            ix = int(round(n["x"] + bw - s_fn(P["block_icon"]) - s_fn(P["block_icon_inset"])))
            iy = int(round(n["y"] + s_fn(P["block_icon_inset"])))
            canvas.alpha_composite(qi, (ix, iy))

    return box(
        dir="col",
        gap=s_fn(P["block_gap"]),
        pad={"t": pad_y, "r": pad_r, "b": pad_y, "l": pad_l},
        bg=paint_bg,
        children=children,
    )


def _apply_shadow_paste(canvas: Image.Image, rect_img: Image.Image, bx: int, by: int, s_fn):
    P = GLASS_STYLE
    blur_r = s_fn(P["shadow_blur"])
    sh_y = s_fn(P["shadow_y"])
    alpha_m = rect_img.split()[3]
    sh_l = Image.new("L", canvas.size, 0)
    sh_l.paste(alpha_m, (bx, by + sh_y))
    sh_l = sh_l.filter(ImageFilter.GaussianBlur(blur_r / 2.0))
    sh_rgba = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
    sh_rgba.putalpha(sh_l.point(lambda p: int(round(p * P["shadow_alpha"]))))
    canvas.alpha_composite(sh_rgba)
    canvas.alpha_composite(rect_img, (bx, by))


# ---- Single-Message Composer & Multi-Message Stacker -----------------------

def render_stage4(
    name: str = "User",
    text: str = "",
    user_id: int = 12345,
    accent_color_id: int | None = None,
    emoji_status: str | None = None,
    entities: list[dict] | None = None,
    custom_emojis: dict | None = None,
    avatar_bytes: bytes | None = None,
    tag: tuple[str, str | None] | None = None,
    reply: dict | None = None,
    media_bytes: bytes | None = None,
    media_type: str | None = None,
    media_duration: float | None = None,
    media_spoiler: bool = False,
    voice: dict | None = None,
    document: dict | None = None,
    audio: dict | None = None,
    via_bot: str | None = None,
    forward_label: str | None = None,
    bg: str | None = None,
    group_pos: str = "single",
    show_avatar: bool = True,
    show_name: bool = True,
    is_quote: bool = False,
) -> Image.Image | None:
    scale = 2
    s = lambda v: int(round(v * scale))
    P = GLASS_STYLE
    width = 512 * scale
    height = 512 * scale

    bg_colors = parse_background_color(bg)
    back_style = light_or_dark(bg_colors["colorOne"])
    light_bubble = back_style == "light"
    palette = NAME_COLORS_LIGHT if light_bubble else NAME_COLORS_DARK
    text_color = "#000" if light_bubble else "#fff"

    name_idx = abs(int(user_id or 0)) % 7
    sender_accent = resolve_accent(accent_color_id, light_bubble)
    name_color = sender_accent[0] if sender_accent else palette[name_idx]
    contrast = get_contrast_ratio(color_luminance(bg_colors["colorOne"], 0.55), name_color)
    if contrast > 90 or contrast < 30:
        name_color = adjust_contrast(color_luminance(bg_colors["colorTwo"], 0.55), name_color)

    font_size = s(P["font_body"])

    # 1. Check 1-3 Big Emoji
    big_emoji_qc = None
    if text and not media_bytes and not voice and not document and not audio and not forward_label:
        raw = str(text).strip()
        no_ws = re.sub(r"\s+", "", raw)
        found = [m.group() for m in qe.EMOJI_RE.finditer(no_ws)]
        if 1 <= len(found) <= 3 and "".join(found) == no_ws:
            big_emoji_qc = draw_multiline_text(raw, [], font_size * 4, text_color, width, font_size * 5, custom_emojis=custom_emojis)

    # 2. Decode Media Image / Big Emoji
    media_img = None
    max_media_size = None
    if big_emoji_qc:
        media_img = big_emoji_qc.image
        media_type = "sticker"
        max_media_size = max(media_img.width, media_img.height)
    elif media_bytes:
        try:
            media_img = Image.open(io.BytesIO(media_bytes)).convert("RGBA")
            if media_spoiler:
                media_img = apply_spoiler(media_img, scale)
            media_type = media_type or "photo"
        except Exception:
            media_img = None

    is_sticker = media_type in ("sticker", "video_note")
    is_round = media_type == "video_note"

    # 3. Name & Header (with optional emoji_status custom emoji badge like index.js line 100)
    header_node = None
    if show_name and name and not is_sticker:
        name_size = s(P["font_name"])
        u16_name_len = sum(2 if ord(c) > 0xFFFF else 1 for c in name)
        name_ents = [{"type": "bold", "offset": 0, "length": u16_name_len}]
        render_name_str = name
        if emoji_status:
            render_name_str = name + " \U0001F921"
            name_ents.append({
                "type": "custom_emoji",
                "offset": u16_name_len + 1,
                "length": 2,
                "custom_emoji_id": str(emoji_status),
            })

        name_qc = draw_multiline_text(render_name_str, name_ents, name_size, name_color, width, name_size, custom_emojis=custom_emojis)
        if not emoji_status and not qe.EMOJI_RE.search(name):
            name_qc = gradient_tint(name_qc, name_color, color_luminance(name_color, 0.25))

        via_qc = draw_label(f"via @{str(via_bot).lstrip('@')}", s(P["font_micro"]), name_color, alpha=0.8) if via_bot else None
        tag_qc = draw_tag(tag, s, text_color) if tag else None
        via_leaf = leaf(via_qc, role="text") if via_qc else None
        tag_leaf = leaf(tag_qc, role="text") if tag_qc else None

        name_max = s(P["max_header"])
        if via_leaf:
            name_max -= via_leaf["w"] + s(P["inline"])
        if tag_leaf:
            name_max -= tag_leaf["w"] + s(P["inline"])
        name_leaf = leaf(name_qc, role="text", maxW=max(s(40), name_max))

        name_side = box(dir="row", align="end", role="text", gap=s(P["inline"]), children=[name_leaf, via_leaf]) if via_leaf else name_leaf
        header_node = (
            box(
                dir="row", justify="between",
                align="center" if (tag_qc and tag_qc.pill) else "end",
                role="text", gap=s(P["inline"]), stretch=True,
                children=[name_side, tag_leaf],
            )
            if tag_leaf else name_side
        )

    # 4. Forward Label
    forward_node = None
    if forward_label and not is_sticker:
        fwd_qc = draw_label(forward_label, s(P["font_micro"]), name_color, bold=True)
        forward_node = leaf(fwd_qc, role="text", maxW=s(P["max_header"]))

    # 5. Reply Block (with striped bar + optional profile background emoji pattern)
    reply_node = None
    if isinstance(reply, dict) and reply.get("name") and (reply.get("text") or reply.get("thumb")):
        r_uid = int(reply.get("user_id") or abs(hash(reply["name"])) or 0)
        r_acc = resolve_accent(reply.get("accent_color_id"), light_bubble)
        r_color = r_acc[0] if r_acc else palette[abs(r_uid) % 7]
        r_stripe = r_acc if (r_acc and len(r_acc) > 1) else None
        r_font_size = s(P["font_reply"])
        r_name_qc = draw_multiline_text(str(reply["name"]), "bold", r_font_size, r_color, width * 0.9, r_font_size, custom_emojis=custom_emojis)
        r_text_qc = (
            draw_multiline_text(str(reply["text"]), reply.get("entities") or [], r_font_size, text_color, width * 0.9, r_font_size, custom_emojis=custom_emojis)
            if reply.get("text") else None
        )
        reply_texts = box(dir="col", gap=s(P["block_gap"]), children=[leaf(r_name_qc, role="text"), leaf(r_text_qc, role="text") if r_text_qc else None])

        thumb_bytes = reply.get("thumb")
        thumb_img = None
        if thumb_bytes:
            try:
                thumb_img = Image.open(io.BytesIO(thumb_bytes)).convert("RGBA")
            except Exception:
                thumb_img = None

        if thumb_img is not None:
            tsize = s(P["reply_thumb"])
            tradius = s(P["block_thumb_radius"])
            def paint_thumb(canvas: Image.Image, n: dict):
                sq = cover_square(n["qc"].image).resize((int(round(n["w"])), int(round(n["h"]))), Image.LANCZOS)
                canvas.alpha_composite(round_image(sq, tradius), (n["x"], n["y"]))
            t_leaf = leaf(thumb_img, trim=False, w=tsize, h=tsize, paint=paint_thumb)
            inner = box(dir="row", gap=s(P["block_thumb_gap"]), align="center", children=[t_leaf, reply_texts])
        else:
            inner = reply_texts

        pattern_em_img = None
        bg_em_id = reply.get("background_emoji_id")
        if bg_em_id and isinstance(custom_emojis, dict) and str(bg_em_id) in custom_emojis:
            raw_pe = custom_emojis[str(bg_em_id)]
            if isinstance(raw_pe, Image.Image):
                pattern_em_img = raw_pe
            elif isinstance(raw_pe, (bytes, bytearray)):
                try:
                    pattern_em_img = Image.open(io.BytesIO(raw_pe)).convert("RGBA")
                except Exception:
                    pattern_em_img = None

        reply_node = accent_block(s, r_color, [inner], icon=False, stripe=r_stripe, pattern_emoji=pattern_em_img)

    # 6. Message Text (and Blockquote splitting)
    stack_gap = lambda a, b: s(P["stack_tight"] if (a["role"] == "text" and b["role"] == "text") else P["stack"])
    text_qc = None
    text_blocks = None
    if text and not big_emoji_qc:
        parts = split_by_blockquotes(text, entities)
        if parts:
            text_blocks = []
            lines_left = P["max_lines"]
            for part in parts:
                if lines_left <= 0:
                    break
                qc = draw_multiline_text(
                    part["text"], part["entities"], font_size, text_color,
                    width, min(height - font_size, max_text_height(font_size, lines_left)),
                    custom_emojis=custom_emojis,
                )
                text_blocks.append({"qc": qc, "quote": part["quote"]})
                asc, desc = font_metrics(font_size)
                used = round((qc.height - asc - desc) / (font_size * 1.2)) + 1 if qc.width > 1 else 0
                lines_left -= max(1, used)
            text_qc = text_blocks[0]["qc"] if text_blocks else None
        else:
            text_qc = draw_multiline_text(
                text, entities, font_size, text_color,
                width, min(height - font_size, max_text_height(font_size, P["max_lines"])),
                custom_emojis=custom_emojis,
            )

    # 7. Attachments (Voice / Document / Audio rows)
    attach_max_w = width * 2.0 / 3.0
    attachment_qc = None
    if isinstance(voice, dict):
        attachment_qc = draw_voice_row(voice.get("waveform"), voice.get("duration", 0), name_color, text_color, scale, attach_max_w)
    elif isinstance(document, dict):
        attachment_qc = draw_document_row(document, name_color, text_color, scale, attach_max_w)
    elif isinstance(audio, dict):
        attachment_qc = draw_audio_row(audio, name_color, text_color, scale, attach_max_w, audio.get("thumb"))

    attachment_node = leaf(attachment_qc) if attachment_qc else None

    # 8. Media Node
    R = s(P["radius"])
    r_small = s(P["radius_grouped"])
    radii = {
        "tl": r_small if group_pos in ("middle", "last") else R,
        "tr": R, "br": R,
        "bl": r_small if group_pos in ("first", "middle") else R,
    }

    has_caption = bool(text_qc and text_qc.width > 1) or bool(text_blocks) or bool(attachment_node)
    media_only = bool(media_img) and not header_node and not has_caption and not reply_node and not forward_node
    flushable = bool(media_img) and not media_only and not is_sticker and not is_round
    flush_bottom = flushable and not has_caption
    flush_top = flushable and not header_node and not forward_node and not reply_node

    media_node = None
    if media_img is not None:
        if max_media_size is None:
            max_media_size = width * 2.0 / 3.0
            if text_qc and max_media_size < text_qc.width:
                max_media_size = float(text_qc.width)

        mw = media_img.width * (max_media_size / max(1, media_img.height))
        mh = max_media_size
        if mw >= max_media_size:
            mw = max_media_size
            mh = media_img.height * (max_media_size / max(1, media_img.width))

        mr = s(P["min_radius"])
        media_radius = (
            s(P["radius"] * 0.6)
            if (media_only or is_sticker)
            else {
                "tl": radii["tl"] if flush_top else mr,
                "tr": radii["tr"] if flush_top else mr,
                "br": radii["br"] if flush_bottom else mr,
                "bl": radii["bl"] if flush_bottom else mr,
            }
        )

        media_badge = None
        if media_type == "video":
            media_badge = {"play": True, "label": format_duration(media_duration) if media_duration is not None else None}
        elif media_type in ("gif", "animation"):
            media_badge = {"label": "GIF"}

        def paint_media(canvas: Image.Image, n: dict):
            dw, dh = max(1, int(round(n["w"]))), max(1, int(round(n["h"])))
            if is_round:
                sq = cover_square(n["qc"].image).resize((dw, dh), Image.LANCZOS)
                mask = Image.new("L", (dw * 2, dh * 2), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, dw * 2 - 1, dh * 2 - 1], fill=255)
                mask = mask.resize((dw, dh), Image.LANCZOS)
                out_m = Image.new("RGBA", (dw, dh), (0, 0, 0, 0))
                out_m.paste(sq, (0, 0), mask)
                canvas.alpha_composite(out_m, (n["x"], n["y"]))
            else:
                scaled = n["qc"].image.resize((dw, dh), Image.LANCZOS)
                canvas.alpha_composite(round_image(scaled, media_radius), (n["x"], n["y"]))
            if media_badge:
                paint_media_badges(canvas, n["x"], n["y"], dw, dh, media_badge, scale)

        side = min(mw, mh)
        media_node = leaf(
            media_img,
            trim=False,
            bleed=(not is_round),
            w=side if is_round else mw,
            h=side if is_round else mh,
            paint=paint_media,
        )

    # 9. Text Node
    text_node = None
    if text_blocks:
        if is_quote:
            runs = [leaf(b["qc"], role="text") for b in text_blocks]
            text_node = accent_block(s, name_color, [box(dir="col", role="text", gap=stack_gap, children=runs)], icon=True)
        else:
            parts_nodes = [
                accent_block(s, name_color, [leaf(b["qc"])], icon=True) if b["quote"] else leaf(b["qc"], role="text")
                for b in text_blocks
            ]
            text_node = box(dir="col", role="text", gap=stack_gap, children=parts_nodes)
    elif text_qc and text_qc.width > 1:
        text_node = accent_block(s, name_color, [leaf(text_qc)], icon=True) if is_quote else leaf(text_qc, role="text")

    if media_node and flushable:
        inner = 0
        for nd in (header_node, forward_node, reply_node, attachment_node, text_node):
            if nd:
                measure(nd)
                inner = max(inner, nd["w"])
        need = inner + 2 * s(P["inset_x"])
        if media_node["w"] < need <= media_node["w"] * 1.25:
            media_node["h"] *= need / media_node["w"]
            media_node["w"] = need

    if not any((header_node, forward_node, reply_node, media_node, attachment_node, text_node)):
        return None

    bubble_pad = {
        "t": 0 if flush_top else s(P["inset_y"]),
        "r": s(P["inset_x"]),
        "b": 0 if flush_bottom else s(P["inset_y"]),
        "l": s(P["inset_x"]),
    }
    tail_size = s(P["tail_size"]) if show_avatar else 0

    def paint_bubble_bg(canvas: Image.Image, n: dict):
        rect = draw_bubble(bg_colors["colorOne"], bg_colors["colorTwo"], n["w"], n["h"], radii, tail_size, s(P["glass_lw"]))
        _apply_shadow_paste(canvas, rect.image, n["x"] - rect._tail_offset, n["y"], s)

    if is_sticker:
        chip = None
        if reply_node:
            def paint_chip_bg(canvas: Image.Image, n: dict):
                rect = draw_bubble(bg_colors["colorOne"], bg_colors["colorTwo"], n["w"], n["h"], s(P["chip_radius"]), 0, s(P["glass_lw"]))
                _apply_shadow_paste(canvas, rect.image, n["x"], n["y"], s)
            chip = box(pad=s(P["chip"]), bg=paint_chip_bg, children=[reply_node])
        root = box(dir="col", gap=stack_gap, children=[chip, media_node])
    else:
        root = box(
            dir="col",
            gap=stack_gap,
            pad=0 if media_only else bubble_pad,
            minW=0 if media_only else s(P["min_width"]),
            bg=paint_bubble_bg,
            children=[header_node, forward_node, reply_node, media_node, attachment_node, text_node],
        )

    measure(root)

    shadow_pad = s(P["shadow_pad"])
    shadow_pad_top = s(P["shadow_pad_top"])
    bubble_pos_x = s(P["avatar"]) + s(P["avatar_gap"])
    canvas_w = int(math.ceil(bubble_pos_x + root["w"] + shadow_pad))
    avatar_h = (s(P["avatar"]) + s(2)) if show_avatar else 0
    canvas_h = int(math.ceil(shadow_pad_top + max(root["h"], avatar_h) + shadow_pad))

    place(root, bubble_pos_x, shadow_pad_top)

    out = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    render_tree(out, root)

    if show_avatar:
        av = draw_avatar(avatar_bytes, name, user_id, s(P["avatar"]))
        av_y = max(0, canvas_h - shadow_pad - s(P["avatar"]) - s(2))
        out.alpha_composite(av, (0, int(round(av_y))))

    return out


def render_messages(messages: list[dict], bg: str | None = None) -> Image.Image:
    """
    Public entry point for ax_quote.py and ax_kang.py.
    Renders 1..N messages using Quotly's grouping rules and scales for Telegram sticker output.
    """
    try:
        if not messages or not isinstance(messages, list):
            return Image.new("RGBA", (512, 128), (0, 0, 0, 0))

        scale = 2
        P = GLASS_STYLE
        rendered = []
        n = len(messages)

        for idx, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            uid = m.get("user_id")
            prev_uid = messages[idx - 1].get("user_id") if (idx > 0 and isinstance(messages[idx - 1], dict)) else None
            next_uid = messages[idx + 1].get("user_id") if (idx + 1 < n and isinstance(messages[idx + 1], dict)) else None

            same_prev = (uid is not None) and (uid == prev_uid)
            same_next = (uid is not None) and (uid == next_uid)

            show_name = not same_prev
            show_avatar = not same_next

            if same_prev and same_next:
                group_pos = "middle"
            elif same_prev:
                group_pos = "last"
            elif same_next:
                group_pos = "first"
            else:
                group_pos = "single"

            canvas = render_stage4(
                name=str(m.get("name") or "User"),
                text=str(m.get("text") or ""),
                user_id=int(uid or 0),
                accent_color_id=m.get("accent_color_id"),
                emoji_status=m.get("emoji_status"),
                entities=m.get("entities") or [],
                custom_emojis=m.get("custom_emojis"),
                avatar_bytes=m.get("avatar"),
                tag=m.get("tag"),
                reply=m.get("reply"),
                media_bytes=m.get("media"),
                media_type=m.get("media_type"),
                media_duration=m.get("media_duration"),
                media_spoiler=bool(m.get("media_spoiler", False)),
                voice=m.get("voice"),
                document=m.get("document"),
                audio=m.get("audio"),
                via_bot=m.get("via_bot"),
                forward_label=m.get("forward_label"),
                bg=bg,
                group_pos=group_pos,
                show_avatar=show_avatar,
                show_name=show_name,
                is_quote=bool(m.get("is_quote", False)),
            )
            if canvas is not None:
                rendered.append((canvas, same_prev))

        if not rendered:
            fallback = render_stage4(name="User", text="…", user_id=0, bg=bg)
            rendered = [(fallback, False)]

        if len(rendered) == 1:
            combined = rendered[0][0]
        else:
            overlap = (P["shadow_pad"] + P["shadow_pad_top"]) * scale
            gap_same = 4 * scale
            gap_diff = 8 * scale
            max_w = max(im.width for im, _ in rendered)
            total_h = rendered[0][0].height
            for im, same_prev in rendered[1:]:
                total_h += im.height + (gap_same if same_prev else gap_diff) - overlap

            combined = Image.new("RGBA", (max_w, max(1, int(round(total_h)))), (0, 0, 0, 0))
            cy = 0
            for i, (im, same_prev) in enumerate(rendered):
                if i > 0:
                    cy += (gap_same if same_prev else gap_diff) - overlap
                combined.alpha_composite(im, (0, max(0, int(round(cy)))))
                cy += im.height

        # Quotly natural sticker scaling:
        # Ensure narrow bubbles (like short multi-line text) are NOT blown up to 512px tall
        # by enforcing a minimum virtual width of 380 * scale before scaling to 512px.
        w, h = combined.size
        min_virtual_w = 380 * scale
        pad_bottom = max(1, round(max(w, h) * 0.06))
        target_w = max(w, min_virtual_w) if h > w else w
        padded = Image.new("RGBA", (target_w, h + pad_bottom), (0, 0, 0, 0))
        padded.paste(combined, (0, 0))
        combined = padded

        w, h = combined.size
        scale_512 = 512.0 / max(1, w, h)
        nw = max(1, min(512, int(round(w * scale_512))))
        nh = max(1, min(512, int(round(h * scale_512))))
        if nw != 512 and nh != 512:
            if nw >= nh:
                nw = 512
            else:
                nh = 512
        return combined.resize((nw, nh), Image.LANCZOS)

    except Exception as e:
        print("quote_pil error:", repr(e))
        return Image.new("RGBA", (512, 128), (0, 0, 0, 0))

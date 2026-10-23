"""Complete Quotly quote-api renderer in pure Python (Pillow)."""
import io, math, os, re, unicodedata
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
import quote_emoji as qe
import quote_fb as qf

NAME_COLORS_LIGHT = ["#FC5C51", "#FA790F", "#895DD5", "#0FB297", "#0FC9D6", "#3CA5EC", "#D54FAF"]
NAME_COLORS_DARK = ["#FF8E86", "#FFA357", "#B18FFF", "#4DD6BF", "#45E8D1", "#7AC9FF", "#FF7FD5"]
AVATAR_COLORS = [
    ("#FF885E", "#FF516A"), ("#FFCD6A", "#FFA85C"), ("#E0A2F3", "#D669ED"),
    ("#A0DE7E", "#54CB68"), ("#53EDD6", "#28C9B7"), ("#72D5FD", "#2A9EF1"),
    ("#FFA8A8", "#FF719A"),
]
TAG_COLORS = {"owner": {"dark": "#b08cf0", "light": "#6d3fc0"}, "admin": {"dark": "#6fcb6d", "light": "#2c8a36"}}

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
    {"dx": 14, "dy": 0.28, "size": 14}, {"dx": 36, "dy": 0.72, "size": 12}, {"dx": 40, "dy": 0.18, "size": 10},
    {"dx": 62, "dy": 0.50, "size": 13}, {"dx": 88, "dy": 0.20, "size": 9}, {"dx": 92, "dy": 0.80, "size": 10},
    {"dx": 120, "dy": 0.50, "size": 8},
]
ENTITY_TYPES_MONOSPACE = {"pre", "code", "pre_code"}
ENTITY_TYPES_MENTION = {"mention", "text_mention", "hashtag", "cashtag", "email", "phone_number", "bot_command", "url", "text_link"}
EMOJI_SCALE = 1.15
CJK_RE = re.compile(r"[\u1100-\u11ff\u2e80-\u2eff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u3100-\u312f\u3130-\u318f\u3190-\u319f\u31a0-\u31bf\u31c0-\u31ef\u31f0-\u31ff\u3200-\u32ff\u3300-\u33ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
RTL_RE = re.compile(r"[\u0591-\u07FF\u200F\u202B\u202E\uFB1D-\uFDFD\uFE70-\uFEFC]")
TALL_SCRIPT_RE = re.compile(r"[\u0590-\u1dff\u2e80-\uffef]")
LEFT_STICKY_PUNCTUATION = set(".,!?:;)]}%\"'\u2019\u201d\u2026\u3001\u3002\uff0c\uff0e\uff01\uff09\uff3d")
KINSOKU_START = set("\u3001\u3002\uff0c\uff0e\u3005\u3009\u300b\u300d\u300f\u3011\u301f\u30fc\u30fd\u30fe\u309d\u309e\uff09\uff3d\uff5d\uff01\uff1f")

GLASS_STYLE = {
    "inset_x": 16, "inset_y": 16, "stack_tight": 12, "stack": 12, "inline": 8,
    "chip": 8, "max_header": 300, "max_lines": 16, "radius": 25, "radius_grouped": 7,
    "min_radius": 4, "block_radius": 9, "chip_radius": 17, "reply_thumb": 40,
    "shadow_pad": 12, "shadow_pad_top": 4, "glass_lw": 2, "tail_size": 14,
    "min_width": 100, "avatar": 50, "avatar_gap": 10,
    "font_body": 24, "font_name": 20, "font_reply": 19, "font_micro": 15, "micro_alpha": 0.65,
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

def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = str(hex_str or "").strip().lstrip("#")
    if len(h) == 3: h = h[0]*2 + h[1]*2 + h[2]*2
    if len(h) != 6: return (41, 34, 50)
    try: return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError: return (41, 34, 50)

def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = [max(0, min(255, int(round(c)))) for c in rgb[:3]]
    return f"#{r:02x}{g:02x}{b:02x}"

def rgba_tuple(hex_str: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    r, g, b = hex_to_rgb(hex_str)
    return (r, g, b, max(0, min(255, int(round(alpha * 255)))))

def color_luminance(hex_str: str, lum: float = 0.0) -> str:
    h = "".join(c for c in str(hex_str) if c.lower() in "0123456789abcdef")
    if len(h) < 6: h = h[0]*2 + h[1]*2 + h[2]*2 if len(h) >= 3 else "292232"
    rgb = "#"
    for i in range(3):
        c = int(h[i*2 : i*2 + 2], 16)
        c = round(min(max(0, c + (c * lum)), 255))
        rgb += f"{c:02x}"
    return rgb

def light_or_dark(color: str) -> str:
    r, g, b = hex_to_rgb(color)
    return "light" if math.sqrt(0.299 * (r * r) + 0.587 * (g * g) + 0.114 * (b * b)) > 127.5 else "dark"

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
    if get_contrast_ratio(bg, fg) >= 4.5: return fg
    if get_brightness(bg) - get_brightness(fg) >= 0:
        return adjust_brightness(fg, math.ceil((175.0 - get_brightness(fg)) / 2.0))
    return adjust_brightness(fg, -math.ceil((get_brightness(fg) - 175.0) / 2.0))

def resolve_accent(accent_id: int | None, is_light: bool) -> list[str] | None:
    if not isinstance(accent_id, int): return None
    colors = (ACCENT_LIGHT if is_light else ACCENT_DARK).get(accent_id)
    if not colors: return None
    if is_light:
        return [c for idx, c in enumerate(colors) if idx == 0 or (0.2126*hex_to_rgb(c)[0] + 0.7152*hex_to_rgb(c)[1] + 0.0722*hex_to_rgb(c)[2])/255.0 < 0.9]
    return list(colors)

def parse_background_color(background_color: str | None = None) -> dict:
    if not isinstance(background_color, str) or not background_color.strip(): background_color = "//#292232"
    background_color = background_color.strip()
    split = background_color.split("/")
    if len(split) > 1 and split[0] != "": return {"colorOne": split[0], "colorTwo": split[1]}
    if background_color.startswith("//"):
        base = background_color.replace("//", "", 1)
        return {"colorOne": color_luminance(base, 0.35), "colorTwo": color_luminance(base, -0.15)}
    return {"colorOne": background_color, "colorTwo": background_color}

_FONT_CACHE = {}
_DUMMY_DR = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
if hasattr(qf, "FB") and "assets/fonts/NotoSansDevanagari.ttf" not in qf.FB:
    qf.FB.insert(0, "assets/fonts/NotoSansDevanagari.ttf")

def _load_style_font(size: int, styles: list[str] | set[str] = ()) -> ImageFont.FreeTypeFont:
    size = max(1, int(round(size)))
    bold, italic, mono = "bold" in styles, "italic" in styles, "monospace" in styles
    key = (size, bold, italic, mono)
    if key in _FONT_CACHE: return _FONT_CACHE[key]
    cands = []
    if mono:
        if bold: cands += ["assets/fonts/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"]
        cands += ["assets/fonts/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
    if bold and italic: cands += ["assets/fonts/DejaVuSans-BoldOblique.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"]
    if bold: cands += ["assets/fonts/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    if italic: cands += ["assets/fonts/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"]
    cands += ["assets/fonts/DejaVuSans.ttf", "assets/fonts/NotoSansDevanagari.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in cands:
        if os.path.isfile(p):
            _FONT_CACHE[key] = ImageFont.truetype(p, size)
            return _FONT_CACHE[key]
    _FONT_CACHE[key] = ImageFont.load_default(size)
    return _FONT_CACHE[key]

def _grapheme_clusters(text: str) -> list[str]:
    clusters = []
    for ch in text:
        if ch in qf.ZW: continue
        if clusters and (unicodedata.category(ch).startswith("M") or (0x0900 <= ord(ch) <= 0x097F and unicodedata.combining(ch))):
            clusters[-1] += ch
        else: clusters.append(ch)
    return clusters

def _runs_with_fallback(text: str, f: ImageFont.FreeTypeFont) -> list[tuple[str, ImageFont.FreeTypeFont]]:
    out = []
    for cl in _grapheme_clusters(text):
        g = qf.pick(f, cl[0])
        if out and out[-1][1] is g: out[-1][0] += cl
        else: out.append([cl, g])
    return [(s, g) for s, g in out]

def _measure_text_width(text: str, f: ImageFont.FreeTypeFont) -> float:
    return sum(_DUMMY_DR.textlength(s, font=g) for s, g in _runs_with_fallback(text, f))

def _draw_text_baseline(dr: ImageDraw.ImageDraw, x: float, baseline_y: float, text: str, f: ImageFont.FreeTypeFont, fill: tuple) -> float:
    for s, g in _runs_with_fallback(text, f):
        dr.text((x, baseline_y), s, font=g, fill=fill, anchor="ls")
        x += _DUMMY_DR.textlength(s, font=g)
    return x

class QCanvas:
    def __init__(self, image: Image.Image, opt_t: float = 0, opt_b: float = 0, pill: bool = False):
        self.image, self.width, self.height = image, image.width, image.height
        self.optical = {"t": max(0, int(round(opt_t))), "b": max(0, int(round(opt_b)))}
        self.pill, self._tail_offset = pill, 0

_METRICS_CACHE, _CAP_CACHE = {}, {}
def font_metrics(font_size: int) -> tuple[int, int]:
    font_size = int(round(font_size))
    if font_size not in _METRICS_CACHE:
        asc, desc = _load_style_font(font_size).getmetrics()
        _METRICS_CACHE[font_size] = (math.ceil(max(asc, font_size * 0.85)), math.ceil(max(desc, font_size * 0.30)))
    return _METRICS_CACHE[font_size]

def cap_height(font_size: int, bold: bool = False) -> int:
    font_size = int(round(font_size))
    key = (font_size, bool(bold))
    if key not in _CAP_CACHE:
        f = _load_style_font(font_size, ("bold",) if bold else ())
        try:
            bbox = _DUMMY_DR.textbbox((0, 0), "H", font=f, anchor="ls")
            _CAP_CACHE[key] = abs(bbox[1]) if bbox[1] < 0 else int(round(font_size * 0.714))
        except Exception: _CAP_CACHE[key] = int(round(font_size * 0.714))
    return _CAP_CACHE[key]

def max_text_height(font_size: int, lines: int) -> float:
    return font_metrics(font_size)[0] + (lines - 0.5) * (font_size * 1.2)

def _arc_points(cx, cy, r, start_deg, end_deg, steps=20):
    if r <= 0: return [(cx, cy)]
    return [(cx + r * math.cos(math.radians(start_deg + (end_deg - start_deg) * (i / steps))),
             cy + r * math.sin(math.radians(start_deg + (end_deg - start_deg) * (i / steps)))) for i in range(steps + 1)]

def _cubic_bezier(p0, p1, p2, p3, steps=28):
    return [((1-i/steps)**3*p0[0] + 3*(1-i/steps)**2*(i/steps)*p1[0] + 3*(1-i/steps)*(i/steps)**2*p2[0] + (i/steps)**3*p3[0],
             (1-i/steps)**3*p0[1] + 3*(1-i/steps)**2*(i/steps)*p1[1] + 3*(1-i/steps)*(i/steps)**2*p2[1] + (i/steps)**3*p3[1]) for i in range(1, steps + 1)]

def _quad_bezier(p0, p1, p2, steps=16):
    return [((1-i/steps)**2*p0[0] + 2*(1-i/steps)*(i/steps)*p1[0] + (i/steps)**2*p2[0],
             (1-i/steps)**2*p0[1] + 2*(1-i/steps)*(i/steps)*p1[1] + (i/steps)**2*p2[1]) for i in range(1, steps + 1)]

def bubble_polygon(w: float, h: float, r: dict | float, tail_size: float, ox: float = 0.0, oy: float = 0.0):
    tl, tr, br, bl = (float(r),)*4 if isinstance(r, (int, float)) else (float(r["tl"]), float(r["tr"]), float(r["br"]), float(r["bl"]))
    cap = lambda v: min(v, w / 2.0, h / 2.0)
    tl, tr, br, bl = cap(tl), cap(tr), cap(br), cap(bl)
    pts = _arc_points(ox + w - tr, oy + tr, tr, -90, 0) + _arc_points(ox + w - br, oy + h - br, br, 0, 90)
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
    if c1[:3] == c2[:3]: return Image.new("RGBA", (w, h), (*c1[:3], 255))
    denom = float(w * w + h * h)
    grid = Image.new("RGBA", (64, 64))
    px = grid.load()
    for gy in range(64):
        y = gy * (h / 63.0)
        for gx in range(64):
            x = gx * (w / 63.0)
            t = max(0.0, min(1.0, (x * w + y * h) / denom))
            px[gx, gy] = (int(round(c1[0]+(c2[0]-c1[0])*t)), int(round(c1[1]+(c2[1]-c1[1])*t)), int(round(c1[2]+(c2[2]-c1[2])*t)), 255)
    return grid.resize((w, h), Image.BICUBIC)

def draw_bubble(color_one: str, color_two: str, w: int, h: int, r: dict | float, tail_size: float = 0, glass_lw: float = 0) -> QCanvas:
    w, h = max(1, int(math.ceil(w))), max(1, int(math.ceil(h)))
    extra_left = int(math.ceil(tail_size * 0.8)) if tail_size > 0 else 0
    total_w, ss = w + extra_left, 3
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
        pts_c = poly + [poly[0]]
        border_ss = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        ImageDraw.Draw(border_ss).line(pts_c, fill=(255, 255, 255, int(round(0.09 * 255))), width=max(1, int(round(glass_lw * 2 * ss))))
        top_ss = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        ImageDraw.Draw(top_ss).line(pts_c, fill=(255, 255, 255, int(round(0.22 * 255))), width=max(1, int(round(glass_lw * 2.6 * ss))))
        fade_h = max(1, int(round(sh * 0.4)))
        grad_l = Image.new("L", (1, sh), 0)
        for y in range(fade_h): grad_l.putpixel((0, y), int(round(255 * (1.0 - y / fade_h))))
        top_ss.putalpha(ImageChops.multiply(top_ss.split()[3], grad_l.resize((sw, sh), Image.NEAREST)))
        glass_ss = Image.alpha_composite(border_ss, top_ss)
        glass_ss.putalpha(ImageChops.multiply(glass_ss.split()[3], mask_ss))
        out = Image.alpha_composite(out, glass_ss.resize((total_w, h), Image.LANCZOS))
    qc = QCanvas(out, 0, 0)
    qc._tail_offset = extra_left
    return qc

def round_image(img: Image.Image, r: dict | float) -> Image.Image:
    w, h = img.size
    if isinstance(r, (int, float)): r = {"tl": r, "tr": r, "br": r, "bl": r}
    poly = bubble_polygon(w * 2, h * 2, {k: v * 2 for k, v in r.items()}, 0, 0, 0)
    mask = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (0, 0), mask.resize((w, h), Image.LANCZOS))
    return out

def cover_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    if w == h: return img.convert("RGBA")
    side = min(w, h)
    return img.convert("RGBA").crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))

def draw_quote_icon(size: int, color: str, alpha: float = 1.0) -> Image.Image:
    size = max(4, int(round(size)))
    s = size * 3
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    fill, r, gap_x = rgba_tuple(color, alpha), s * 0.09, s * 0.36
    for ox in (s * 0.08, s * 0.08 + gap_x):
        oy = s * 0.15
        cx, cy = ox + r, oy + r
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
        p0, c1, p1 = (ox + r * 2, cy), (ox + r * 2.2, oy + s * 0.35), (ox + r * 0.3, oy + s * 0.4)
        p2, c2, p3 = (ox + r * 0.3, oy + s * 0.32), (ox + r * 1.5, oy + s * 0.27), (ox + r * 0.8, cy)
        dr.polygon([p0] + _quad_bezier(p0, c1, p1) + [p2] + _quad_bezier(p2, c2, p3), fill=fill)
    return im.resize((size, size), Image.LANCZOS)

def gradient_tint(qc: QCanvas, color_from: str, color_to: str) -> QCanvas:
    if not qc or qc.width < 2: return qc
    w, h = qc.width, qc.height
    c1, c2 = hex_to_rgb(color_from), hex_to_rgb(color_to)
    grad = Image.new("RGBA", (w, 1))
    for x in range(w):
        t = x / max(1, w - 1)
        grad.putpixel((x, 0), (int(round(c1[0]+(c2[0]-c1[0])*t)), int(round(c1[1]+(c2[1]-c1[1])*t)), int(round(c1[2]+(c2[2]-c1[2])*t)), 255))
    grad = grad.resize((w, h), Image.NEAREST)
    grad.putalpha(qc.image.split()[3])
    return QCanvas(grad, qc.optical["t"], qc.optical["b"], pill=qc.pill)

def _extract_initials(name: str) -> str:
    clean = qe.clean(str(name or "")).strip()
    words = [w for w in clean.upper().split() if w]
    if not words: return "?"
    c0 = _grapheme_clusters(words[0])
    first = c0[0] if c0 else "?"
    if len(words) > 1:
        cl = _grapheme_clusters(words[-1])
        return first + (cl[0] if cl else "")
    return first

def draw_avatar(avatar_bytes: bytes | None, name: str, user_id: int, size: int) -> Image.Image:
    size = max(16, int(round(size)))
    mask = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 2 - 1, size * 2 - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    if avatar_bytes:
        try:
            fitted = ImageOps.fit(Image.open(io.BytesIO(avatar_bytes)).convert("RGBA"), (size, size), Image.LANCZOS)
            out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            out.paste(fitted, (0, 0), mask)
            return out
        except Exception: pass
    c1, c2 = AVATAR_COLORS[abs(int(user_id or 0)) % 7]
    bg = _linear_gradient_2d(size, size, hex_to_rgb(c1), hex_to_rgb(c2))
    letters = _extract_initials(name)
    f = _load_style_font(int(round(size * (0.38 if len(_grapheme_clusters(letters)) > 1 else 0.48))), ("bold",))
    runs = _runs_with_fallback(letters, f)
    ImageDraw.Draw(bg).text((size / 2.0, size / 2.0), letters, font=(runs[0][1] if runs else f), fill=(255, 255, 255, 255), anchor="mm")
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    return out

def draw_label(text: str, font_size: int, color: str, bold: bool = False, alpha: float = 1.0) -> QCanvas:
    font_size = int(round(font_size))
    text = qe.clean(str(text or ""))
    asc, desc = font_metrics(font_size)
    f = _load_style_font(font_size, ("bold",) if bold else ())
    w, h = max(1, int(math.ceil(_measure_text_width(text, f)))), max(1, int(math.ceil(asc + desc)))
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    _draw_text_baseline(ImageDraw.Draw(im), 0, asc, text, f, rgba_tuple(color, alpha))
    return QCanvas(im, asc - cap_height(font_size, bold), h - asc)

def draw_tag(tag_tuple: tuple[str, str | None], s_fn, text_color: str) -> QCanvas | None:
    if not tag_tuple: return None
    role, custom_title = tag_tuple[0], tag_tuple[1] if len(tag_tuple) > 1 else None
    label_text = custom_title if custom_title else role
    if not label_text: return None
    size = s_fn(GLASS_STYLE["font_micro"])
    colors = TAG_COLORS.get(role)
    if not colors: return draw_label(label_text, size, text_color, alpha=GLASS_STYLE["micro_alpha"])
    color = colors["light"] if text_color == "#000" else colors["dark"]
    label = draw_label(label_text, size, color, bold=False)
    o, pad_x, pad_y = label.optical, s_fn(GLASS_STYLE["tag_pad_x"]), s_fn(GLASS_STYLE["tag_pad_y"])
    w, h = int(math.ceil(label.width + 2 * pad_x)), int(math.ceil(label.height - o["t"] - o["b"] + 2 * pad_y))
    pill_bg = draw_bubble(color, color, w, h, h / 2.0, 0, 0).image
    pill_bg.putalpha(pill_bg.split()[3].point(lambda p: int(round(p * GLASS_STYLE["tag_tint"]))))
    pill_bg.alpha_composite(label.image, (int(round(pad_x)), int(round(pad_y - o["t"]))))
    return QCanvas(pill_bg, 0, 0, pill=True)

def format_duration(seconds: float | int) -> str:
    total = max(0, int(round(float(seconds or 0))))
    return f"{total // 60}:{total % 60:02d}"

def format_file_size(num_bytes: int | float) -> str:
    b = max(0.0, float(num_bytes or 0))
    if b < 1024: return f"{int(round(b))} B"
    v, u, units = b, -1, ["KB", "MB", "GB"]
    while True:
        v /= 1024.0; u += 1
        if v < 1024.0 or u >= len(units) - 1: break
    return f"{v:.1f} {units[u]}"

def _draw_play_disc(d: int, accent: str) -> Image.Image:
    d, sd = max(8, int(round(d))), max(8, int(round(d))) * 2
    im = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.ellipse([0, 0, sd - 1, sd - 1], fill=rgba_tuple(accent, 1.0))
    r, cx, cy = sd * 0.22, sd * 0.54, sd / 2.0
    dr.polygon([(cx - r * 0.7, cy - r), (cx - r * 0.7, cy + r), (cx + r * 1.1, cy)], fill=(255, 255, 255, 255))
    return im.resize((d, d), Image.LANCZOS)

def _draw_file_disc(d: int, accent: str) -> Image.Image:
    d, sd = max(8, int(round(d))), max(8, int(round(d))) * 2
    im = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
    dr, acc = ImageDraw.Draw(im), rgba_tuple(accent, 1.0)
    dr.ellipse([0, 0, sd - 1, sd - 1], fill=acc)
    w, h = sd * 0.34, sd * 0.44
    x, y, fold = (sd - w) / 2.0, (sd - h) / 2.0, w * 0.38
    dr.polygon([(x, y), (x + w - fold, y), (x + w, y + fold), (x + w, y + h), (x, y + h)], fill=(255, 255, 255, 255))
    dr.polygon([(x + w - fold, y), (x + w - fold, y + fold), (x + w, y + fold)], fill=acc)
    return im.resize((d, d), Image.LANCZOS)

def _draw_note_disc(d: int, accent: str) -> Image.Image:
    d, sd = max(8, int(round(d))), max(8, int(round(d))) * 2
    im = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.ellipse([0, 0, sd - 1, sd - 1], fill=rgba_tuple(accent, 1.0))
    hr, hx, hy, sh, sw = sd * 0.09, sd * 0.42, sd * 0.66, sd * 0.34, max(2, sd * 0.045)
    dr.ellipse([hx - hr * 1.25, hy - hr, hx + hr * 1.25, hy + hr], fill=(255, 255, 255, 255))
    dr.rectangle([hx + hr * 1.1 - sw, hy - sh, hx + hr * 1.1, hy], fill=(255, 255, 255, 255))
    return im.resize((d, d), Image.LANCZOS)

def _synthetic_waveform(duration: float, n: int) -> list[int]:
    a = ((int(round(float(duration or 0))) * 2654435761) + 0x9E3779B9) & 0xFFFFFFFF
    def rnd():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((a ^ (a >> 15)) * (a | 1)) & 0xFFFFFFFF
        t = (t ^ (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF))) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0
    p1, p2, f1, f2 = rnd() * math.pi * 2.0, rnd() * math.pi * 2.0, 2.0 + rnd() * 2.0, 9.0 + rnd() * 5.0
    out = []
    for i in range(n):
        x = (i / (n - 1)) if n > 1 else 0.0
        v = (0.55 + 0.35 * math.sin(x * math.pi * f1 + p1)) * (0.3 + 0.7 * (0.5 + 0.5 * math.sin(i * f2 / 4.0 + p2))) * 1.15 + (rnd() - 0.5) * 0.4
        if math.sin(x * math.pi * (f1 * 2.3) + p2) > 0.93: v *= 0.25
        out.append(int(round(min(1.0, max(0.08, v)) * 31)))
    return out

def _resample_waveform(data: list | None, n: int) -> list[float]:
    clean = [min(31.0, max(0.0, float(v))) for v in (data or []) if isinstance(v, (int, float))]
    if not clean: return [0.0] * n
    if len(clean) <= n: return [v / 31.0 for v in clean]
    return [sum(clean[int(i*len(clean)/n):max(int(i*len(clean)/n)+1, int((i+1)*len(clean)/n))]) / (max(int(i*len(clean)/n)+1, int((i+1)*len(clean)/n)) - int(i*len(clean)/n)) / 31.0 for i in range(n)]

def draw_voice_row(waveform: list | None, duration: float, accent: str, text_color: str, scale: int, max_width: float) -> QCanvas:
    P, s = GLASS_STYLE, lambda v: v * scale
    d = int(round(s(P["row_disc"])))
    dur_label = draw_label(format_duration(duration), int(round(s(P["font_micro"]))), text_color, alpha=P["micro_alpha"])
    pitch = s(P["row_bar"]) + s(P["row_bar_gap"])
    fit = max(8, int(math.floor(max(pitch * 8, (max_width or s(220)) - d - s(P["row_gap"]) * 2 - dur_label.width) / pitch)))
    has_wf = isinstance(waveform, list) and any(isinstance(v, (int, float)) and v > 0 for v in waveform)
    bar_count = min(max(8, len(waveform)), fit) if has_wf else min(fit, max(16, int(round(float(duration or 0) * 2))))
    heights = _resample_waveform(waveform if has_wf else _synthetic_waveform(duration, bar_count), bar_count)
    bars_w = bar_count * pitch - s(P["row_bar_gap"])
    w, h = int(math.ceil(d + s(P["row_gap"]) + bars_w + s(P["row_gap"]) + dur_label.width)), d
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.alpha_composite(_draw_play_disc(d, accent), (0, 0))
    dr, acc_rgba, cy, bar_w = ImageDraw.Draw(canvas), rgba_tuple(accent, 1.0), h / 2.0, s(P["row_bar"])
    for i in range(bar_count):
        bh = max(s(P["row_bar_min"]), heights[i] * s(P["row_bar_max"]))
        bx = d + s(P["row_gap"]) + i * pitch
        dr.rounded_rectangle([bx, cy - bh / 2.0, bx + bar_w, cy + bh / 2.0], radius=bar_w / 2.0, fill=acc_rgba)
    o = dur_label.optical
    canvas.alpha_composite(dur_label.image, (int(round(d + s(P["row_gap"]) + bars_w + s(P["row_gap"]))), int(round(h / 2.0 - (o["t"] + dur_label.height - o["b"]) / 2.0))))
    return QCanvas(canvas, 0, 0)

def _fit_label(text: str, font_size: int, bold: bool, max_w: float, mode: str = "end") -> str:
    if not max_w or max_w <= 0: return text
    f = _load_style_font(font_size, ("bold",) if bold else ())
    if _measure_text_width(text, f) <= max_w: return text
    chars, ell = _grapheme_clusters(text), "…"
    n = len(chars)
    while n > 1 and _measure_text_width("".join(chars[:n]).rstrip() + ell, f) > max_w: n -= 1
    return "".join(chars[:n]).rstrip() + ell

def _assemble_row(disc: Image.Image, title: QCanvas, meta: QCanvas | None, scale: int, max_width: float) -> QCanvas:
    P, s = GLASS_STYLE, lambda v: v * scale
    gap = s(P["row_gap"])
    w = int(math.ceil(disc.width + gap + max(title.width, meta.width if meta else 0)))
    if max_width and w > max_width: w = int(math.ceil(max_width))
    vis = lambda c: c.height - c.optical["t"] - c.optical["b"]
    line_gap = s(P["row_line_gap"])
    texts_h = vis(title) + (line_gap + vis(meta) if meta else 0)
    h = int(math.ceil(max(disc.height, texts_h)))
    pad_t, pad_b = title.optical["t"], (meta or title).optical["b"]
    h_all = int(math.ceil(pad_t + h + pad_b))
    canvas = Image.new("RGBA", (w, h_all), (0, 0, 0, 0))
    canvas.alpha_composite(disc, (0, int(round(pad_t + (h - disc.height) / 2.0))))
    ty, tx = int(round(pad_t + (h - texts_h) / 2.0)), int(round(disc.width + gap))
    canvas.alpha_composite(title.image, (tx, int(round(ty - title.optical["t"]))))
    if meta: canvas.alpha_composite(meta.image, (tx, int(round(ty + vis(title) + line_gap - meta.optical["t"]))))
    return QCanvas(canvas, pad_t, h_all - pad_t - h)

def draw_document_row(doc: dict, accent: str, text_color: str, scale: int, max_width: float) -> QCanvas:
    P, s = GLASS_STYLE, lambda v: int(round(v * scale))
    d, name = s(P["row_disc"]), str(doc.get("file_name") or "File")
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else ""
    meta_text = " · ".join(p for p in [format_file_size(doc["file_size"]) if doc.get("file_size") is not None else None, ext or None] if p)
    text_max = (max_width - d - s(P["row_gap"])) if max_width else 0
    title = draw_label(_fit_label(name, s(P["row_title"]), True, text_max), s(P["row_title"]), text_color, bold=True)
    meta = draw_label(meta_text, s(P["font_micro"]), text_color, alpha=P["micro_alpha"]) if meta_text else None
    return _assemble_row(_draw_file_disc(d, accent), title, meta, scale, max_width)

def draw_audio_row(audio: dict, accent: str, text_color: str, scale: int, max_width: float, thumb_bytes: bytes | None = None) -> QCanvas:
    P, s = GLASS_STYLE, lambda v: int(round(v * scale))
    d, lead = s(P["row_disc"]), None
    if thumb_bytes:
        try: lead = round_image(cover_square(Image.open(io.BytesIO(thumb_bytes)).convert("RGBA")).resize((d, d), Image.LANCZOS), s(P["row_cover"]))
        except Exception: lead = None
    if lead is None: lead = _draw_note_disc(d, accent)
    meta_text = " · ".join(str(p) for p in [audio.get("performer") or None, format_duration(audio["duration"]) if audio.get("duration") is not None else None] if p)
    text_max = (max_width - d - s(P["row_gap"])) if max_width else 0
    title = draw_label(_fit_label(str(audio.get("title") or "Audio"), s(P["row_title"]), True, text_max), s(P["row_title"]), text_color, bold=True)
    meta = draw_label(meta_text, s(P["font_micro"]), text_color, alpha=P["micro_alpha"]) if meta_text else None
    return _assemble_row(lead, title, meta, scale, max_width)

def paint_media_badges(canvas: Image.Image, x: int, y: int, w: int, h: int, badge: dict | None, scale: int):
    if not badge: return
    P, s = GLASS_STYLE, lambda v: v * scale
    if badge.get("play"):
        d = int(round(min(s(P["badge_play"]), w * 0.45, h * 0.45)))
        sd = d * 2
        disc = Image.new("RGBA", (sd, sd), (0, 0, 0, 0))
        dr = ImageDraw.Draw(disc)
        dr.ellipse([0, 0, sd - 1, sd - 1], fill=(0, 0, 0, int(round(P["badge_bg"] * 255))))
        r, cx, cy = sd * 0.24, sd / 2.0, sd / 2.0
        dr.polygon([(cx - r * 0.62, cy - r), (cx - r * 0.62, cy + r), (cx + r * 1.05, cy)], fill=(255, 255, 255, 255))
        canvas.alpha_composite(disc.resize((d, d), Image.LANCZOS), (int(round(x + (w - d) / 2.0)), int(round(y + (h - d) / 2.0))))
    if badge.get("label"):
        lbl = draw_label(str(badge["label"]), int(round(s(P["font_micro"]))), "#ffffff")
        o, pad_x, pad_y = lbl.optical, s(P["badge_pad_x"]), s(P["badge_pad_y"])
        bw, bh = int(math.ceil(lbl.width + pad_x * 2)), int(math.ceil(lbl.height - o["t"] - o["b"] + pad_y * 2))
        pill = draw_bubble("#000000", "#000000", bw, bh, bh / 2.0, 0, 0).image
        pill.putalpha(pill.split()[3].point(lambda p: int(round(p * P["badge_bg"]))))
        pill.alpha_composite(lbl.image, (int(round(pad_x)), int(round(pad_y - o["t"]))))
        canvas.alpha_composite(pill, (int(round(x + s(P["badge_inset"]))), int(round(y + h - bh - s(P["badge_inset"])))))

def apply_spoiler(img: Image.Image, scale: int = 2) -> Image.Image:
    k = min(1.0, 800 / max(1, img.width, img.height))
    w, h = max(2, int(round(img.width * k))), max(2, int(round(img.height * k)))
    src = Image.new("RGBA", (w, h), (128, 128, 128, 255))
    src.alpha_composite(img.convert("RGBA").resize((w, h), Image.LANCZOS))
    tw, th = max(2, int(round(9 if w >= h else (9 * w) / h))), max(2, int(round(9 if h > w else (9 * h) / w)))
    blurred = src.resize((tw, th), Image.BICUBIC).resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(8))
    out = Image.alpha_composite(blurred, Image.new("RGBA", (w, h), (30, 34, 44, int(round(0.28 * 255)))))
    seed = ((w * 73856093) ^ (h * 19349663)) & 0xFFFFFFFF
    def rnd():
        nonlocal seed
        seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
        return seed / 4294967296.0
    dot = max(1.0, (scale * w) / 640.0 * 1.1)
    dr = ImageDraw.Draw(out)
    for _ in range(int(round((w * h) / (dot * dot * 22.0)))):
        px, py, pr = rnd() * w, rnd() * h, dot * (0.45 + rnd() * 0.75)
        dr.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(255, 255, 255, int(round((0.35 + rnd() * 0.5) * 255))))
    return out

def _utf16_to_char_map(text: str) -> list[int]:
    m = []
    for idx, ch in enumerate(text):
        m.append(idx)
        if ord(ch) > 0xFFFF: m.append(idx)
    m.append(len(text))
    return m

def split_by_blockquotes(text: str, entities: list[dict] | None) -> list[dict] | None:
    if not isinstance(entities, list) or not entities: return None
    quotes = sorted([e for e in entities if isinstance(e, dict) and e.get("type") in ("blockquote", "expandable_blockquote")], key=lambda e: e.get("offset", 0))
    if not quotes: return None
    u_map = _utf16_to_char_map(text)
    max_u = len(u_map) - 1
    def slice_e(u_start: int, u_end: int):
        out = []
        for e in entities:
            if not isinstance(e, dict) or e.get("type") in ("blockquote", "expandable_blockquote"): continue
            eo, el = int(e.get("offset", 0)), int(e.get("length", 0))
            if eo < u_end and eo + el > u_start:
                f, t = max(eo, u_start), min(eo + el, u_end)
                out.append({**e, "offset": f - u_start, "length": t - f})
        return out
    parts, pos = [], 0
    for q in quotes:
        qo = max(0, min(max_u, int(q.get("offset", 0))))
        ql = max(0, min(max_u - qo, int(q.get("length", 0))))
        if qo < pos: continue
        if qo > pos:
            plain = text[u_map[pos]:u_map[qo]].rstrip("\n")
            if plain: parts.append({"text": plain, "entities": slice_e(pos, qo), "quote": False})
        parts.append({"text": text[u_map[qo]:u_map[qo + ql]], "entities": slice_e(qo, qo + ql), "quote": True})
        pos = qo + ql
    if pos < max_u:
        tail = text[u_map[pos]:].lstrip("\n")
        if tail: parts.append({"text": tail, "entities": slice_e(pos, max_u), "quote": False})
    return parts or None

def prepare_text(text: str, entities: list[dict] | str | None, font_size: int, custom_emojis: dict | None = None) -> dict:
    font_size = int(round(font_size))
    asc, desc = font_metrics(font_size)
    lh, em_size = font_size * 1.2, font_size * EMOJI_SCALE
    if not text:
        return {"segments": [], "fontSize": font_size, "lineHeight": lh, "emojiSize": em_size, "ascent": asc, "descent": desc}
    text = text.replace("і", "i")
    n = len(text)
    char_styles, char_ce = [[] for _ in range(n)], [None] * n
    if isinstance(entities, str):
        for s in char_styles: s.append(entities)
    elif isinstance(entities, list):
        u_map = _utf16_to_char_map(text)
        max_u = len(u_map) - 1
        for ent in entities:
            if not isinstance(ent, dict): continue
            et = ent.get("type", "")
            st = "monospace" if et in ENTITY_TYPES_MONOSPACE else ("mention" if et in ENTITY_TYPES_MENTION else et)
            o = max(0, min(max_u, int(ent.get("offset", 0))))
            l = max(0, min(max_u - o, int(ent.get("length", 0))))
            cs, ce = u_map[o], u_map[o + l]
            if et == "custom_emoji" and ent.get("custom_emoji_id") and cs < n:
                cid = str(ent["custom_emoji_id"])
                for i in range(cs, ce): char_ce[i] = cid
            for i in range(cs, ce):
                if st not in char_styles[i]: char_styles[i].append(st)
    clean_text = "".join(qe.clean(c) for c in text)
    while len(char_styles) < len(clean_text):
        char_styles.append(list(char_styles[-1]) if char_styles else [])
        char_ce.append(None)
    emoji_at = [None] * len(clean_text)
    for e_idx, m in enumerate(qe.EMOJI_RE.finditer(clean_text)):
        code = m.group()
        for i in range(m.start(), m.end()):
            if i < len(emoji_at): emoji_at[i] = (e_idx, code, char_ce[i])
    for i, cid in enumerate(char_ce):
        if cid and emoji_at[i] is None: emoji_at[i] = (10000 + i, clean_text[i], cid)
    segments = []
    for m in re.finditer(r"\r\n|\r|\n|[^\S\r\n]+|\w+|[^\w\s]", clean_text):
        start, end = m.start(), m.end()
        sub_s = start
        for i in range(start, end):
            prev_i = i - 1 if i > sub_s else None
            if clean_text[i] in ("\r", "\n") or (prev_i is not None and (emoji_at[i] != emoji_at[prev_i] or char_styles[i] != char_styles[prev_i])):
                if i > sub_s: _push_segment(segments, clean_text, char_styles, emoji_at, sub_s, i, font_size, em_size, custom_emojis)
                sub_s = i
        if sub_s < end: _push_segment(segments, clean_text, char_styles, emoji_at, sub_s, end, font_size, em_size, custom_emojis)
    cjk_segs = []
    for seg in segments:
        if seg["kind"] != "text" or not CJK_RE.search(seg["text"]):
            cjk_segs.append(seg); continue
        cls = _grapheme_clusters(seg["text"])
        if len(cls) <= 1: cjk_segs.append(seg); continue
        for cl in cls: cjk_segs.append({**seg, "text": cl, "width": _measure_text_width(cl, seg["font"])})
    merged = []
    for seg in cjk_segs:
        if seg["kind"] == "text" and len(seg["text"]) == 1 and (seg["text"] in LEFT_STICKY_PUNCTUATION or seg["text"] in KINSOKU_START) and merged and merged[-1]["kind"] == "text" and merged[-1]["styles"] == seg["styles"]:
            merged[-1]["text"] += seg["text"]
            merged[-1]["width"] = _measure_text_width(merged[-1]["text"], merged[-1]["font"])
        else: merged.append(seg)
    return {"segments": merged, "fontSize": font_size, "lineHeight": lh, "emojiSize": em_size, "ascent": asc, "descent": desc}

def _push_segment(out: list, text: str, char_styles: list, emoji_at: list, start: int, end: int, font_size: int, emoji_size: float, custom_emojis: dict | None):
    sub, styles, em = text[start:end], list(char_styles[start]), emoji_at[start]
    kind = "break" if sub in ("\r", "\n", "\r\n") else ("space" if sub.isspace() else ("emoji" if em is not None else "text"))
    f = _load_style_font(font_size, styles)
    em_img = None
    if kind == "emoji":
        cid = em[2]
        if cid and isinstance(custom_emojis, dict) and cid in custom_emojis:
            raw_ce = custom_emojis[cid]
            if isinstance(raw_ce, Image.Image): em_img = raw_ce
            elif isinstance(raw_ce, (bytes, bytearray)):
                try: em_img = Image.open(io.BytesIO(raw_ce)).convert("RGBA")
                except Exception: em_img = None
        if em_img is None: em_img = qe.fetch(em[1])
    w = emoji_size if kind == "emoji" else (0.0 if kind == "break" else _measure_text_width(sub, f))
    out.append({"text": sub, "kind": kind, "styles": styles, "font": f, "emojiImage": em_img, "width": w})

def _get_line_direction(segments: list, start_index: int) -> str:
    for i in range(start_index, len(segments)):
        if segments[i]["kind"] in ("space", "break"): continue
        return "rtl" if RTL_RE.search(segments[i]["text"]) else "ltr"
    return "ltr"

def _apply_truncation(line: dict, prepared: dict, max_width: float):
    if not line["segments"]:
        line["width"], line["truncated"] = 0, True
        return
    segments = prepared["segments"]
    ell_w = _measure_text_width("…", segments[line["segments"][-1]["index"]]["font"])
    target_w, total_w, trim_idx = max_width - ell_w, 0.0, len(line["segments"])
    for idx, s in enumerate(line["segments"]):
        if total_w + s["width"] > target_w: trim_idx = idx; break
        total_w += s["width"]
    line["segments"] = line["segments"][:max(1, trim_idx)]
    while len(line["segments"]) > 1 and segments[line["segments"][-1]["index"]]["kind"] == "space":
        total_w -= line["segments"][-1]["width"]
        line["segments"].pop()
    line["width"] = (total_w if total_w > 0 else line["segments"][0]["width"]) + ell_w
    line["truncated"] = True

def layout_text(prepared: dict, max_width: float, max_height: float) -> dict:
    segments, lh, em_size, asc, desc = prepared["segments"], prepared["lineHeight"], prepared["emojiSize"], prepared["ascent"], prepared["descent"]
    if not segments: return {"lines": [], "width": 0, "height": 0, "lineCount": 0, "truncated": False}
    max_width, max_height = min(10000.0, max(1.0, float(max_width))), (min(10000.0, float(max_height)) if max_height > 0 else 0.0)
    lines, cur, line_y, max_line_w, truncated, pending_break, i = [], {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, 0), "truncated": False}, float(asc), 0.0, False, None, 0
    while i < len(segments):
        seg = segments[i]
        if seg["kind"] == "break":
            if (i + 1) < len(segments) and max_height > 0 and (line_y + lh > max_height):
                truncated = True; _apply_truncation(cur, prepared, max_width); break
            max_line_w = max(max_line_w, cur["width"]); cur["y"] = line_y; lines.append(cur); line_y += lh; pending_break = None
            cur = {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, i + 1), "truncated": False}; i += 1; continue
        seg_w = em_size if seg["kind"] == "emoji" else seg["width"]
        if seg["kind"] == "space" and cur["width"] + seg_w > max_width:
            cur["segments"].append({"index": i, "width": seg_w}); i += 1; continue
        if not cur["segments"] and seg_w > max_width and seg["kind"] == "text" and len(seg["text"]) > 1:
            cls = _grapheme_clusters(seg["text"])
            if len(cls) > 1:
                c_w, cursor = [_measure_text_width(c, seg["font"]) for c in cls], 0
                while cursor < len(cls):
                    fit_c, fit_w = 0, 0.0
                    for g in range(cursor, len(cls)):
                        if fit_w + c_w[g] > max_width and fit_c > 0: break
                        fit_w += c_w[g]; fit_c += 1
                    if fit_c == 0: fit_c, fit_w = 1, c_w[cursor]
                    is_last = (cursor + fit_c) >= len(cls)
                    cur["segments"].append({"index": i, "width": fit_w, "sub_text": "".join(cls[cursor:cursor + fit_c])})
                    cur["width"] += fit_w
                    if not is_last:
                        if max_height > 0 and (line_y + lh > max_height):
                            truncated = True; _apply_truncation(cur, prepared, max_width); break
                        max_line_w = max(max_line_w, cur["width"]); cur["y"] = line_y; lines.append(cur); line_y += lh
                        cur = {"segments": [], "width": 0.0, "direction": cur["direction"], "truncated": False}
                    cursor += fit_c
                if truncated: break
                i += 1; continue
        if cur["width"] + seg_w > max_width and cur["segments"]:
            if max_height > 0 and (line_y + lh > max_height):
                truncated = True; _apply_truncation(cur, prepared, max_width); break
            if pending_break is not None:
                b_idx, b_w, b_cnt = pending_break
                cur["segments"], cur["width"] = cur["segments"][:b_cnt], b_w
                max_line_w = max(max_line_w, cur["width"]); cur["y"] = line_y; lines.append(cur); line_y += lh; pending_break = None
                cur = {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, b_idx + 1), "truncated": False}
                i = b_idx + 1; continue
            max_line_w = max(max_line_w, cur["width"]); cur["y"] = line_y; lines.append(cur); line_y += lh
            cur = {"segments": [], "width": 0.0, "direction": _get_line_direction(segments, i), "truncated": False}
            if seg["kind"] != "space": cur["segments"].append({"index": i, "width": seg_w}); cur["width"] += seg_w
            pending_break = None; i += 1; continue
        if seg["kind"] == "space": pending_break = (i, cur["width"], len(cur["segments"]))
        cur["segments"].append({"index": i, "width": seg_w}); cur["width"] += seg_w; i += 1
    if cur["segments"]:
        max_line_w = max(max_line_w, cur["width"]); cur["y"] = line_y; lines.append(cur)
    for line in lines:
        x = 0.0
        for lseg in line["segments"]: lseg["x"] = x; x += lseg["width"]
    return {"lines": lines, "width": max_line_w, "height": (lines[-1]["y"] + desc) if lines else 0, "lineCount": len(lines), "truncated": truncated}

def shrink_wrap(prepared: dict, max_width: float, max_height: float) -> dict:
    initial = layout_text(prepared, max_width, max_height)
    if initial["lineCount"] <= 1 or initial["truncated"]: return initial
    lo = max(max((s["width"] for s in prepared["segments"]), default=0.0), initial["width"] / initial["lineCount"] * 0.7)
    hi, target = initial["width"], initial["lineCount"]
    while hi - lo > 2.0:
        mid = (lo + hi) / 2.0
        trial = layout_text(prepared, mid, max_height)
        if trial["lineCount"] <= target and not trial["truncated"]: hi = mid
        else: lo = mid
    return layout_text(prepared, math.ceil(hi), max_height)

def render_text(layout_res: dict, prepared: dict, font_color: str) -> QCanvas:
    lines, width, height = layout_res["lines"], layout_res["width"], layout_res["height"]
    segments, font_size, emoji_size = prepared["segments"], prepared["fontSize"], int(round(prepared["emojiSize"]))
    if not lines or width <= 0: return QCanvas(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)
    canvas = Image.new("RGBA", (max(1, int(math.ceil(width))), max(1, int(math.ceil(height)))), (0, 0, 0, 0))
    dr = ImageDraw.Draw(canvas)
    for line_idx, line in enumerate(lines):
        for lseg in line["segments"]:
            seg = segments[lseg["index"]]
            st = seg["styles"]
            fill = rgba_tuple("#5887a7", 1.0) if "monospace" in st else (rgba_tuple("#6ab7ec", 1.0) if "mention" in st else (rgba_tuple(font_color, 0.15) if "spoiler" in st else rgba_tuple(font_color, 1.0)))
            draw_x = (width - lseg["x"] - (emoji_size if seg["kind"] == "emoji" else lseg["width"])) if line["direction"] == "rtl" else lseg["x"]
            draw_y = line["y"]
            if seg["kind"] == "emoji":
                if seg["emojiImage"] is not None:
                    canvas.alpha_composite(seg["emojiImage"].resize((emoji_size, emoji_size), Image.LANCZOS), (int(round(draw_x)), int(round(draw_y - font_size + font_size * 0.15))))
                else: _draw_text_baseline(dr, draw_x, draw_y, seg["text"], seg["font"], fill)
            elif seg["kind"] not in ("space", "break"):
                draw_str = lseg.get("sub_text", seg["text"])
                _draw_text_baseline(dr, draw_x, draw_y, draw_str, seg["font"], fill)
                tw_val = _measure_text_width(draw_str, seg["font"])
                if "strikethrough" in st: dr.rectangle([draw_x, draw_y - font_size / 2.8, draw_x + tw_val, draw_y - font_size / 2.8 + font_size * 0.1], fill=fill)
                if "underline" in st: dr.rectangle([draw_x, draw_y + 2, draw_x + tw_val, draw_y + 2 + font_size * 0.1], fill=fill)
        if line_idx == len(lines) - 1 and line.get("truncated") and line["segments"]:
            last_lseg = line["segments"][-1]
            last_seg = segments[last_lseg["index"]]
            ell_x = (width - last_lseg["x"] - last_lseg["width"] - _measure_text_width("…", last_seg["font"])) if line["direction"] == "rtl" else (last_lseg["x"] + last_lseg["width"])
            _draw_text_baseline(dr, ell_x, line["y"], "…", last_seg["font"], rgba_tuple(font_color, 1.0))
    first_line, last_line = lines[0], lines[-1]
    any_bold = any(s["kind"] == "text" and "bold" in s["styles"] for s in segments)
    first_items = [segments[ls["index"]] for ls in first_line["segments"] if segments[ls["index"]]["kind"] not in ("space", "break")]
    if first_items and all(s["kind"] == "emoji" for s in first_items): top_val = font_size * 0.85
    else:
        top_val = float(cap_height(font_size, any_bold))
        for s in first_items:
            if s["kind"] == "text" and TALL_SCRIPT_RE.search(s["text"]):
                for sub_s, g_font in _runs_with_fallback(s["text"], s["font"]):
                    try: top_val = max(top_val, float(abs(_DUMMY_DR.textbbox((0, 0), sub_s, font=g_font, anchor="ls")[1])))
                    except Exception: pass
    last_items = [segments[ls["index"]] for ls in last_line["segments"]]
    emoji_bottom = (emoji_size - font_size * 0.85) if last_items and all(s["kind"] == "emoji" for s in last_items) else 0.0
    return QCanvas(canvas, first_line["y"] - top_val, math.ceil(height) - last_line["y"] - emoji_bottom)

def draw_multiline_text(text: str, entities: list[dict] | str | None, font_size: int, font_color: str, max_width: float, max_height: float, custom_emojis: dict | None = None) -> QCanvas:
    prepared = prepare_text(text, entities, font_size, custom_emojis=custom_emojis)
    if not prepared["segments"]: return QCanvas(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)
    return render_text(shrink_wrap(prepared, max_width, max_height), prepared, font_color)

def _norm_pad(pad) -> dict:
    if not pad: return {"t": 0, "r": 0, "b": 0, "l": 0}
    if isinstance(pad, (int, float)): return {"t": pad, "r": pad, "b": pad, "l": pad}
    return {"t": pad.get("t", 0), "r": pad.get("r", 0), "b": pad.get("b", 0), "l": pad.get("l", 0)}

def leaf(qc: QCanvas | Image.Image | None, **opts) -> dict | None:
    if qc is None: return None
    if isinstance(qc, Image.Image): qc = QCanvas(qc, 0, 0)
    if qc.width <= 1 and qc.height <= 1: return None
    w = min(opts.get("w", qc.width), opts["maxW"]) if opts.get("maxW") else opts.get("w", qc.width)
    use_opt = opts.get("optical", True) and opts.get("trim", True) and ("h" not in opts)
    trim_t, trim_b = (qc.optical["t"], qc.optical["b"]) if use_opt else (0, 0)
    return {"kind": "leaf", "qc": qc, "trimT": trim_t, "role": opts.get("role", "block"), "w": w, "h": opts.get("h", qc.height) - trim_t - trim_b, "bleed": bool(opts.get("bleed", False)), "paint": opts.get("paint")}

def box(**opts) -> dict:
    return {"kind": "box", "dir": opts.get("dir", "col"), "gap": opts.get("gap", 0), "role": opts.get("role", "block"), "pad": _norm_pad(opts.get("pad")), "align": opts.get("align", "start"), "justify": opts.get("justify", "start"), "stretch": bool(opts.get("stretch", False)), "bg": opts.get("bg"), "fg": opts.get("fg"), "minW": opts.get("minW", 0), "maxW": opts.get("maxW", 0), "children": [c for c in (opts.get("children") or []) if c is not None]}

def _gap_before(b_node: dict, child: dict, idx: int) -> float:
    if idx == 0: return 0
    if "mt" in child: return child["mt"]
    g = b_node["gap"]
    return g(b_node["children"][idx - 1], child) if callable(g) else g

def measure(n: dict) -> dict:
    if n["kind"] == "leaf": return n
    for c in n["children"]: measure(c)
    w, h, p = 0.0, 0.0, n["pad"]
    if n["dir"] == "col":
        for i, c in enumerate(n["children"]):
            w = max(w, c["w"] if c.get("bleed") else (c["w"] + p["l"] + p["r"]))
            h += _gap_before(n, c, i) + c["h"]
    else:
        for c in n["children"]: h = max(h, c["h"]); w += c["w"]
        w += (n["gap"] if not callable(n["gap"]) else 0) * max(0, len(n["children"]) - 1) + p["l"] + p["r"]
    n["w"] = max(int(math.ceil(w)), int(math.ceil(n["minW"])))
    n["h"] = int(math.ceil(h + p["t"] + p["b"]))
    if n["maxW"] and n["w"] > n["maxW"]: n["w"] = int(math.ceil(n["maxW"]))
    return n

def place(n: dict, x: float, y: float, stretch_w: float | None = None):
    if stretch_w is not None and n.get("stretch"): n["w"] = int(round(stretch_w))
    n["x"], n["y"] = int(round(x)), int(round(y))
    x, y = n["x"], n["y"]
    if n["kind"] == "leaf": return
    p, inner_w = n["pad"], n["w"] - n["pad"]["l"] - n["pad"]["r"]
    if n["dir"] == "col":
        cy = y + p["t"]
        for i, c in enumerate(n["children"]):
            cx = (x + max(0.0, (n["w"] - c["w"]) / 2.0)) if c.get("bleed") else (x + p["l"] + ((inner_w - c["w"]) / 2.0 if (n["align"] == "center" and c["w"] < inner_w) else 0))
            cy += _gap_before(n, c, i); place(c, cx, cy, inner_w); cy += c["h"]
    else:
        cross_y = lambda c: (y + p["t"] + (n["h"] - p["t"] - p["b"] - c["h"]) / 2.0) if n["align"] == "center" else ((y + n["h"] - p["b"] - c["h"]) if n["align"] == "end" else (y + p["t"]))
        gap_val = n["gap"] if not callable(n["gap"]) else 0
        if n["justify"] == "between" and len(n["children"]) == 2:
            a, b = n["children"]
            if a["w"] > inner_w - b["w"] - gap_val: a["w"] = max(0, int(round(inner_w - b["w"] - gap_val)))
            place(a, x + p["l"], cross_y(a)); place(b, x + n["w"] - p["r"] - b["w"], cross_y(b)); return
        cx = x + p["l"]
        for c in n["children"]: place(c, cx, cross_y(c)); cx += c["w"] + gap_val

def _fade_overflow(leaf_node: dict) -> Image.Image:
    im, w, h = leaf_node["qc"].image, max(1, int(round(leaf_node["w"]))), leaf_node["qc"].image.height
    alpha, cut = im.split()[3].load(), min(w, im.width)
    for x in range(cut - 1, int(cut * 0.4), -1):
        if not any(alpha[x, y] > 8 for y in range(h)): cut = x; break
    return im.crop((0, 0, max(1, cut), h))

def render_tree(canvas: Image.Image, n: dict):
    if n.get("bg"): n["bg"](canvas, n)
    if n["kind"] == "leaf":
        if n.get("paint"): n["paint"](canvas, n)
        else:
            im = _fade_overflow(n) if n["qc"].image.width > n["w"] + 1 else n["qc"].image
            canvas.alpha_composite(im, (int(round(n["x"])), int(round(n["y"] - n["trimT"]))))
    else:
        for c in n["children"]: render_tree(canvas, c)
    if n.get("fg"): n["fg"](canvas, n)

def _paint_stripe(bar_img: Image.Image, colors: list[str], s_fn) -> Image.Image:
    w, h = bar_img.size
    dash, slant = s_fn(GLASS_STYLE["stripe_dash"]) * (0.85 if len(colors) > 2 else 1.0), s_fn(GLASS_STYLE["stripe_slant"])
    stripe_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr, y, i = ImageDraw.Draw(stripe_layer), -float(slant), 0
    while y < h + slant:
        dr.polygon([(0, y + slant), (w, y), (w, y + dash), (0, y + dash + slant)], fill=rgba_tuple(colors[i % len(colors)], 1.0))
        y += dash; i += 1
    stripe_layer.putalpha(ImageChops.multiply(stripe_layer.split()[3], bar_img.split()[3]))
    return stripe_layer

def _paint_emoji_pattern(canvas: Image.Image, n: dict, emoji_img: Image.Image, accent: str, shape: Image.Image, s_fn):
    bw, bh = int(math.ceil(n["w"])), int(math.ceil(n["h"]))
    clip, acc_rgb, bar_limit = Image.new("RGBA", (bw, bh), (0, 0, 0, 0)), hex_to_rgb(accent), bw - s_fn(GLASS_STYLE["block_bar"]) * 2
    for cell in EMOJI_CELLS:
        size = max(2, int(round(s_fn(cell["size"]))))
        if s_fn(cell["dx"]) + size > bar_limit: continue
        em_s = emoji_img.convert("RGBA").resize((size, size), Image.LANCZOS)
        tinted = Image.new("RGBA", (size, size), (*acc_rgb, 255))
        tinted.putalpha(em_s.split()[3])
        clip.alpha_composite(tinted, (int(round(bw - s_fn(cell["dx"]) - size / 2.0)), int(round(bh * cell["dy"] - size / 2.0))))
    clip.putalpha(ImageChops.multiply(clip.split()[3], shape.split()[3]).point(lambda p: int(round(p * GLASS_STYLE["emoji_alpha"]))))
    canvas.alpha_composite(clip, (n["x"], n["y"]))

def accent_block(s_fn, accent: str, children: list, icon: bool = False, stripe: list[str] | None = None, pattern_emoji: Image.Image | None = None) -> dict:
    P = GLASS_STYLE
    bar_w, radius = int(math.ceil(s_fn(P["block_bar"]))), s_fn(P["block_radius"])
    def paint_bg(canvas: Image.Image, n: dict):
        bw, bh = int(math.ceil(n["w"])), int(math.ceil(n["h"]))
        solid = draw_bubble(accent, accent, bw, bh, radius, 0, 0).image
        tinted = solid.copy()
        tinted.putalpha(tinted.split()[3].point(lambda p: int(round(p * P["block_tint"]))))
        canvas.alpha_composite(tinted, (n["x"], n["y"]))
        bar_img = solid.crop((0, 0, min(bar_w, bw), bh))
        if stripe and len(stripe) > 1: bar_img = _paint_stripe(bar_img, stripe, s_fn)
        canvas.alpha_composite(bar_img, (n["x"], n["y"]))
        if pattern_emoji is not None: _paint_emoji_pattern(canvas, n, pattern_emoji, accent, solid, s_fn)
        if icon:
            canvas.alpha_composite(draw_quote_icon(s_fn(P["block_icon"]), accent, 1.0), (int(round(n["x"] + bw - s_fn(P["block_icon"]) - s_fn(P["block_icon_inset"]))), int(round(n["y"] + s_fn(P["block_icon_inset"])))))
    return box(dir="col", gap=s_fn(P["block_gap"]), pad={"t": s_fn(P["block_pad_y"]), "r": s_fn(P["block_pad_r_icon"] if icon else P["block_pad_r"]), "b": s_fn(P["block_pad_y"]), "l": s_fn(P["block_pad_l"])}, bg=paint_bg, children=children)

def _apply_shadow_paste(canvas: Image.Image, rect_img: Image.Image, bx: int, by: int, s_fn):
    P = GLASS_STYLE
    sh_l = Image.new("L", canvas.size, 0)
    sh_l.paste(rect_img.split()[3], (bx, by + s_fn(P["shadow_y"])))
    sh_l = sh_l.filter(ImageFilter.GaussianBlur(s_fn(P["shadow_blur"]) / 2.0))
    sh_rgba = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
    sh_rgba.putalpha(sh_l.point(lambda p: int(round(p * P["shadow_alpha"]))))
    canvas.alpha_composite(sh_rgba)
    canvas.alpha_composite(rect_img, (bx, by))

def render_stage4(name: str = "User", text: str = "", user_id: int = 12345, accent_color_id: int | None = None, emoji_status: str | None = None, entities: list[dict] | None = None, custom_emojis: dict | None = None, avatar_bytes: bytes | None = None, tag: tuple[str, str | None] | None = None, reply: dict | None = None, media_bytes: bytes | None = None, media_type: str | None = None, media_duration: float | None = None, media_spoiler: bool = False, voice: dict | None = None, document: dict | None = None, audio: dict | None = None, via_bot: str | None = None, forward_label: str | None = None, bg: str | None = None, group_pos: str = "single", show_avatar: bool = True, show_name: bool = True, is_quote: bool = False) -> Image.Image | None:
    scale = 2
    s, P, width, height = lambda v: int(round(v * scale)), GLASS_STYLE, 512 * scale, 512 * scale
    bg_colors = parse_background_color(bg)
    light_bubble = light_or_dark(bg_colors["colorOne"]) == "light"
    palette, text_color = (NAME_COLORS_LIGHT if light_bubble else NAME_COLORS_DARK), ("#000" if light_bubble else "#fff")
    sender_accent = resolve_accent(accent_color_id, light_bubble)
    name_color = sender_accent[0] if sender_accent else palette[abs(int(user_id or 0)) % 7]
    if get_contrast_ratio(color_luminance(bg_colors["colorOne"], 0.55), name_color) in range(0, 30) or get_contrast_ratio(color_luminance(bg_colors["colorOne"], 0.55), name_color) > 90:
        name_color = adjust_contrast(color_luminance(bg_colors["colorTwo"], 0.55), name_color)
    font_size = s(P["font_body"])
    big_emoji_qc = None
    if text and not media_bytes and not voice and not document and not audio and not forward_label:
        raw, no_ws = str(text).strip(), re.sub(r"\s+", "", str(text).strip())
        found = [m.group() for m in qe.EMOJI_RE.finditer(no_ws)]
        if 1 <= len(found) <= 3 and "".join(found) == no_ws:
            big_emoji_qc = draw_multiline_text(raw, [], font_size * 4, text_color, width, font_size * 5, custom_emojis=custom_emojis)
    media_img, max_media_size = None, None
    if big_emoji_qc:
        media_img, media_type, max_media_size = big_emoji_qc.image, "sticker", max(big_emoji_qc.width, big_emoji_qc.height)
    elif media_bytes:
        try:
            media_img = Image.open(io.BytesIO(media_bytes)).convert("RGBA")
            if media_spoiler: media_img = apply_spoiler(media_img, scale)
            media_type = media_type or "photo"
        except Exception: media_img = None
    is_sticker, is_round = media_type in ("sticker", "video_note"), media_type == "video_note"
    header_node = None
    if show_name and name and not is_sticker:
        name_size, u16_len = s(P["font_name"]), sum(2 if ord(c) > 0xFFFF else 1 for c in name)
        name_ents, r_name = [{"type": "bold", "offset": 0, "length": u16_len}], name
        if emoji_status:
            r_name = name + " \U0001F921"
            name_ents.append({"type": "custom_emoji", "offset": u16_len + 1, "length": 2, "custom_emoji_id": str(emoji_status)})
        name_qc = draw_multiline_text(r_name, name_ents, name_size, name_color, width, name_size, custom_emojis=custom_emojis)
        if not emoji_status and not qe.EMOJI_RE.search(name):
            name_qc = gradient_tint(name_qc, name_color, color_luminance(name_color, 0.25))
        via_qc = draw_label(f"via @{str(via_bot).lstrip('@')}", s(P["font_micro"]), name_color, alpha=0.8) if via_bot else None
        tag_qc = draw_tag(tag, s, text_color) if tag else None
        via_leaf, tag_leaf = (leaf(via_qc, role="text") if via_qc else None), (leaf(tag_qc, role="text") if tag_qc else None)
        name_max = s(P["max_header"]) - (via_leaf["w"] + s(P["inline"]) if via_leaf else 0) - (tag_leaf["w"] + s(P["inline"]) if tag_leaf else 0)
        name_leaf = leaf(name_qc, role="text", maxW=max(s(40), name_max))
        name_side = box(dir="row", align="end", role="text", gap=s(P["inline"]), children=[name_leaf, via_leaf]) if via_leaf else name_leaf
        header_node = box(dir="row", justify="between", align="center" if (tag_qc and tag_qc.pill) else "end", role="text", gap=s(P["inline"]), stretch=True, children=[name_side, tag_leaf]) if tag_leaf else name_side
    forward_node = leaf(draw_label(forward_label, s(P["font_micro"]), name_color, bold=True), role="text", maxW=s(P["max_header"])) if (forward_label and not is_sticker) else None
    reply_node = None
    if isinstance(reply, dict) and reply.get("name") and (reply.get("text") or reply.get("thumb")):
        r_uid = int(reply.get("user_id") or abs(hash(reply["name"])) or 0)
        r_acc = resolve_accent(reply.get("accent_color_id"), light_bubble)
        r_color = r_acc[0] if r_acc else palette[abs(r_uid) % 7]
        r_fs = s(P["font_reply"])
        r_name_qc = draw_multiline_text(str(reply["name"]), "bold", r_fs, r_color, width * 0.9, r_fs, custom_emojis=custom_emojis)
        r_text_qc = draw_multiline_text(str(reply["text"]), reply.get("entities") or [], r_fs, text_color, width * 0.9, r_fs, custom_emojis=custom_emojis) if reply.get("text") else None
        reply_texts = box(dir="col", gap=s(P["block_gap"]), children=[leaf(r_name_qc, role="text"), leaf(r_text_qc, role="text") if r_text_qc else None])
        thumb_img = None
        if reply.get("thumb"):
            try: thumb_img = Image.open(io.BytesIO(reply["thumb"])).convert("RGBA")
            except Exception: thumb_img = None
        if thumb_img is not None:
            tsize, tradius = s(P["reply_thumb"]), s(P["block_thumb_radius"])
            t_leaf = leaf(thumb_img, trim=False, w=tsize, h=tsize, paint=lambda cv, n: cv.alpha_composite(round_image(cover_square(n["qc"].image).resize((int(round(n["w"])), int(round(n["h"]))), Image.LANCZOS), tradius), (n["x"], n["y"])))
            inner = box(dir="row", gap=s(P["block_thumb_gap"]), align="center", children=[t_leaf, reply_texts])
        else: inner = reply_texts
        pattern_em = None
        bg_em_id = reply.get("background_emoji_id")
        if bg_em_id and isinstance(custom_emojis, dict) and str(bg_em_id) in custom_emojis:
            raw_pe = custom_emojis[str(bg_em_id)]
            try: pattern_em = raw_pe if isinstance(raw_pe, Image.Image) else Image.open(io.BytesIO(raw_pe)).convert("RGBA")
            except Exception: pattern_em = None
        reply_node = accent_block(s, r_color, [inner], icon=False, stripe=(r_acc if r_acc and len(r_acc) > 1 else None), pattern_emoji=pattern_em)
    stack_gap = lambda a, b: s(P["stack_tight"] if (a["role"] == "text" and b["role"] == "text") else P["stack"])
    text_qc, text_blocks = None, None
    if text and not big_emoji_qc:
        parts = split_by_blockquotes(text, entities)
        if parts:
            text_blocks, lines_left = [], P["max_lines"]
            for part in parts:
                if lines_left <= 0: break
                qc = draw_multiline_text(part["text"], part["entities"], font_size, text_color, width, min(height - font_size, max_text_height(font_size, lines_left)), custom_emojis=custom_emojis)
                text_blocks.append({"qc": qc, "quote": part["quote"]})
                asc, desc = font_metrics(font_size)
                lines_left -= max(1, round((qc.height - asc - desc) / (font_size * 1.2)) + 1 if qc.width > 1 else 0)
            text_qc = text_blocks[0]["qc"] if text_blocks else None
        else:
            text_qc = draw_multiline_text(text, entities, font_size, text_color, width, min(height - font_size, max_text_height(font_size, P["max_lines"])), custom_emojis=custom_emojis)
    attach_max_w = width * 2.0 / 3.0
    attachment_qc = draw_voice_row(voice.get("waveform"), voice.get("duration", 0), name_color, text_color, scale, attach_max_w) if isinstance(voice, dict) else (draw_document_row(document, name_color, text_color, scale, attach_max_w) if isinstance(document, dict) else (draw_audio_row(audio, name_color, text_color, scale, attach_max_w, audio.get("thumb")) if isinstance(audio, dict) else None))
    attachment_node = leaf(attachment_qc) if attachment_qc else None
    R, r_small = s(P["radius"]), s(P["radius_grouped"])
    radii = {"tl": r_small if group_pos in ("middle", "last") else R, "tr": R, "br": R, "bl": r_small if group_pos in ("first", "middle") else R}
    has_caption = bool(text_qc and text_qc.width > 1) or bool(text_blocks) or bool(attachment_node)
    media_only = bool(media_img) and not header_node and not has_caption and not reply_node and not forward_node
    flushable = bool(media_img) and not media_only and not is_sticker and not is_round
    flush_bottom, flush_top = flushable and not has_caption, flushable and not header_node and not forward_node and not reply_node
    media_node = None
    if media_img is not None:
        if max_media_size is None:
            max_media_size = max(width * 2.0 / 3.0, float(text_qc.width) if text_qc else 0.0)
        mw, mh = media_img.width * (max_media_size / max(1, media_img.height)), max_media_size
        if mw >= max_media_size: mw, mh = max_media_size, media_img.height * (max_media_size / max(1, media_img.width))
        mr = s(P["min_radius"])
        media_radius = s(P["radius"] * 0.6) if (media_only or is_sticker) else {"tl": radii["tl"] if flush_top else mr, "tr": radii["tr"] if flush_top else mr, "br": radii["br"] if flush_bottom else mr, "bl": radii["bl"] if flush_bottom else mr}
        media_badge = {"play": True, "label": format_duration(media_duration) if media_duration is not None else None} if media_type == "video" else ({"label": "GIF"} if media_type in ("gif", "animation") else None)
        def paint_media(canvas: Image.Image, n: dict):
            dw, dh = max(1, int(round(n["w"]))), max(1, int(round(n["h"])))
            if is_round:
                sq = cover_square(n["qc"].image).resize((dw, dh), Image.LANCZOS)
                mask = Image.new("L", (dw * 2, dh * 2), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, dw * 2 - 1, dh * 2 - 1], fill=255)
                out_m = Image.new("RGBA", (dw, dh), (0, 0, 0, 0))
                out_m.paste(sq, (0, 0), mask.resize((dw, dh), Image.LANCZOS))
                canvas.alpha_composite(out_m, (n["x"], n["y"]))
            else:
                canvas.alpha_composite(round_image(n["qc"].image.resize((dw, dh), Image.LANCZOS), media_radius), (n["x"], n["y"]))
            if media_badge: paint_media_badges(canvas, n["x"], n["y"], dw, dh, media_badge, scale)
        media_node = leaf(media_img, trim=False, bleed=(not is_round), w=(min(mw, mh) if is_round else mw), h=(min(mw, mh) if is_round else mh), paint=paint_media)
    text_node = None
    if text_blocks:
        text_node = accent_block(s, name_color, [box(dir="col", role="text", gap=stack_gap, children=[leaf(b["qc"], role="text") for b in text_blocks])], icon=True) if is_quote else box(dir="col", role="text", gap=stack_gap, children=[accent_block(s, name_color, [leaf(b["qc"])], icon=True) if b["quote"] else leaf(b["qc"], role="text") for b in text_blocks])
    elif text_qc and text_qc.width > 1:
        text_node = accent_block(s, name_color, [leaf(text_qc)], icon=True) if is_quote else leaf(text_qc, role="text")
    if media_node and flushable:
        inner = max((measure(nd)["w"] for nd in (header_node, forward_node, reply_node, attachment_node, text_node) if nd), default=0)
        need = inner + 2 * s(P["inset_x"])
        if media_node["w"] < need <= media_node["w"] * 1.25:
            media_node["h"] *= need / media_node["w"]; media_node["w"] = need
    if not any((header_node, forward_node, reply_node, media_node, attachment_node, text_node)): return None
    bubble_pad = {"t": 0 if flush_top else s(P["inset_y"]), "r": s(P["inset_x"]), "b": 0 if flush_bottom else s(P["inset_y"]), "l": s(P["inset_x"])}
    tail_size = s(P["tail_size"]) if show_avatar else 0
    def paint_bubble_bg(canvas: Image.Image, n: dict):
        rect = draw_bubble(bg_colors["colorOne"], bg_colors["colorTwo"], n["w"], n["h"], radii, tail_size, s(P["glass_lw"]))
        _apply_shadow_paste(canvas, rect.image, n["x"] - rect._tail_offset, n["y"], s)
    if is_sticker:
        chip = box(pad=s(P["chip"]), bg=lambda cv, n: _apply_shadow_paste(cv, draw_bubble(bg_colors["colorOne"], bg_colors["colorTwo"], n["w"], n["h"], s(P["chip_radius"]), 0, s(P["glass_lw"])).image, n["x"], n["y"], s), children=[reply_node]) if reply_node else None
        root = box(dir="col", gap=stack_gap, children=[chip, media_node])
    else:
        root = box(dir="col", gap=stack_gap, pad=(0 if media_only else bubble_pad), minW=(0 if media_only else s(P["min_width"])), bg=paint_bubble_bg, children=[header_node, forward_node, reply_node, media_node, attachment_node, text_node])
    measure(root)
    shadow_pad, shadow_pad_top, bubble_pos_x = s(P["shadow_pad"]), s(P["shadow_pad_top"]), s(P["avatar"]) + s(P["avatar_gap"])
    canvas_w = int(math.ceil(bubble_pos_x + root["w"] + shadow_pad))
    canvas_h = int(math.ceil(shadow_pad_top + max(root["h"], (s(P["avatar"]) + s(2)) if show_avatar else 0) + shadow_pad))
    place(root, bubble_pos_x, shadow_pad_top)
    out = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    render_tree(out, root)
    if show_avatar:
        out.alpha_composite(draw_avatar(avatar_bytes, name, user_id, s(P["avatar"])), (0, int(round(max(0, canvas_h - shadow_pad - s(P["avatar"]) - s(2))))))
    return out

def render_messages(messages: list[dict], bg: str | None = None) -> Image.Image:
    try:
        if not messages or not isinstance(messages, list): return Image.new("RGBA", (512, 128), (0, 0, 0, 0))
        scale, P, rendered, n = 2, GLASS_STYLE, [], len(messages)
        for idx, m in enumerate(messages):
            if not isinstance(m, dict): continue
            uid = m.get("user_id")
            prev_uid = messages[idx - 1].get("user_id") if (idx > 0 and isinstance(messages[idx - 1], dict)) else None
            next_uid = messages[idx + 1].get("user_id") if (idx + 1 < n and isinstance(messages[idx + 1], dict)) else None
            same_prev, same_next = (uid is not None and uid == prev_uid), (uid is not None and uid == next_uid)
            group_pos = "middle" if (same_prev and same_next) else ("last" if same_prev else ("first" if same_next else "single"))
            cv = render_stage4(name=str(m.get("name") or "User"), text=str(m.get("text") or ""), user_id=int(uid or 0), accent_color_id=m.get("accent_color_id"), emoji_status=m.get("emoji_status"), entities=m.get("entities") or [], custom_emojis=m.get("custom_emojis"), avatar_bytes=m.get("avatar"), tag=m.get("tag"), reply=m.get("reply"), media_bytes=m.get("media"), media_type=m.get("media_type"), media_duration=m.get("media_duration"), media_spoiler=bool(m.get("media_spoiler", False)), voice=m.get("voice"), document=m.get("document"), audio=m.get("audio"), via_bot=m.get("via_bot"), forward_label=m.get("forward_label"), bg=bg, group_pos=group_pos, show_avatar=(not same_next), show_name=(not same_prev), is_quote=bool(m.get("is_quote", False)))
            if cv is not None: rendered.append((cv, same_prev))
        if not rendered:
            rendered = [(render_stage4(name="User", text="…", user_id=0, bg=bg), False)]
        if len(rendered) == 1:
            combined = rendered[0][0]
        else:
            overlap, gap_same, gap_diff = (P["shadow_pad"] + P["shadow_pad_top"]) * scale, 4 * scale, 8 * scale
            max_w = max(im.width for im, _ in rendered)
            total_h = rendered[0][0].height + sum(im.height + (gap_same if sp else gap_diff) - overlap for im, sp in rendered[1:])
            combined, cy = Image.new("RGBA", (max_w, max(1, int(round(total_h)))), (0, 0, 0, 0)), 0
            for i, (im, sp) in enumerate(rendered):
                if i > 0: cy += (gap_same if sp else gap_diff) - overlap
                combined.alpha_composite(im, (0, max(0, int(round(cy))))); cy += im.height
        w, h = combined.size
        pad_b = max(1, round(max(w, h) * 0.06))
        target_w = max(w, 380 * scale) if h > w else w
        padded = Image.new("RGBA", (target_w, h + pad_b), (0, 0, 0, 0))
        padded.paste(combined, (0, 0))
        w, h = padded.size
        s512 = 512.0 / max(1, w, h)
        nw, nh = max(1, min(512, int(round(w * s512)))), max(1, min(512, int(round(h * s512))))
        if nw != 512 and nh != 512:
            if nw >= nh: nw = 512
            else: nh = 512
        return padded.resize((nw, nh), Image.LANCZOS)
    except Exception as e:
        print("quote_pil error:", repr(e))
        return Image.new("RGBA", (512, 128), (0, 0, 0, 0))

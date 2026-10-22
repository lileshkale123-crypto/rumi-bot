"""Quotly-style quote sticker renderer in pure Python (Pillow)."""
import io
import math
import re

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

import quote_emoji as qe
import quote_fb as qf
import quote_layout as L

# ---- Quotly Palettes & Constants -------------------------------------------

NAME_COLORS_LIGHT = [
    "#FC5C51", "#FA790F", "#895DD5", "#0FB297", "#0FC9D6", "#3CA5EC", "#D54FAF"
]
NAME_COLORS_DARK = [
    "#FF8E86", "#FFA357", "#B18FFF", "#4DD6BF", "#45E8D1", "#7AC9FF", "#FF7FD5"
]
AVATAR_COLORS = [
    ("#FF885E", "#FF516A"),
    ("#FFCD6A", "#FFA85C"),
    ("#E0A2F3", "#D669ED"),
    ("#A0DE7E", "#54CB68"),
    ("#53EDD6", "#28C9B7"),
    ("#72D5FD", "#2A9EF1"),
    ("#FFA8A8", "#FF719A"),
]

TAG_COLORS = {
    "owner": {"dark": "#b08cf0", "light": "#6d3fc0"},
    "admin": {"dark": "#6fcb6d", "light": "#2c8a36"},
}

ENTITY_MONOSPACE = {"pre", "code", "pre_code"}
ENTITY_MENTION = {
    "mention", "text_mention", "hashtag", "cashtag",
    "email", "phone_number", "bot_command", "url", "text_link",
}

EMOJI_SCALE = 1.15
SCALE = 2
DEFAULT_BG = "#1b1429"

CJK_RE = re.compile(
    r"[\u1100-\u11ff\u2e80-\u2eff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff"
    r"\u3100-\u312f\u3130-\u318f\u3190-\u319f\u31a0-\u31bf\u31c0-\u31ef"
    r"\u31f0-\u31ff\u3200-\u32ff\u3300-\u33ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uac00-\ud7af\uf900-\ufaff]"
)
RTL_RE = re.compile(r"[\u0591-\u07FF\u200F\u202B\u202E\uFB1D-\uFDFD\uFE70-\uFEFC]")
TALL_SCRIPT_RE = re.compile(r"[\u0590-\u1dff\u2e80-\uffef]")

LEFT_STICKY_PUNCT = set(".,!?:;)]}%\"'\u2019\u201d\u2026\u3001\u3002\uff0c\uff0e\uff01\uff09\uff3d")


# ---- Color Utilities -------------------------------------------------------

def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = str(hex_str or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (27, 20, 41)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (27, 20, 41)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = [max(0, min(255, int(round(c)))) for c in rgb[:3]]
    return f"#{r:02x}{g:02x}{b:02x}"


def rgba_tuple(hex_str: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    r, g, b = hex_to_rgb(hex_str)
    return (r, g, b, max(0, min(255, int(round(alpha * 255)))))


def color_luminance(hex_str: str, lum: float = 0.0) -> str:
    r, g, b = hex_to_rgb(hex_str)
    r = max(0, min(255, int(round(r + r * lum))))
    g = max(0, min(255, int(round(g + g * lum))))
    b = max(0, min(255, int(round(b + b * lum))))
    return rgb_to_hex((r, g, b))


def light_or_dark(hex_str: str) -> str:
    r, g, b = hex_to_rgb(hex_str)
    hsp = math.sqrt(0.299 * (r * r) + 0.587 * (g * g) + 0.114 * (b * b))
    return "light" if hsp > 127.5 else "dark"


def get_brightness(hex_str: str) -> float:
    r, g, b = hex_to_rgb(hex_str)
    return (r * 299 + g * 587 + b * 114) / 1000.0


def adjust_brightness(hex_str: str, amount: float) -> str:
    r, g, b = hex_to_rgb(hex_str)
    return rgb_to_hex((r + amount, g + amount, b + amount))


def get_contrast_ratio(bg: str, fg: str) -> float:
    b1 = get_brightness(bg)
    b2 = get_brightness(fg)
    return (max(b1, b2) + 0.05) / (min(b1, b2) + 0.05)


def adjust_contrast(bg: str, fg: str) -> str:
    ratio = get_contrast_ratio(bg, fg)
    diff = get_brightness(bg) - get_brightness(fg)
    if ratio >= 4.5:
        return fg
    threshold = 175.0
    if diff >= 0:
        amt = math.ceil((threshold - get_brightness(fg)) / 2.0)
        return adjust_brightness(fg, amt)
    amt = math.ceil((get_brightness(fg) - threshold) / 2.0)
    return adjust_brightness(fg, -amt)


def parse_bg_color(bg: str | None) -> tuple[str, str]:
    if not bg or not isinstance(bg, str):
        bg = DEFAULT_BG
    bg = bg.strip()
    if "/" in bg and not bg.startswith("//"):
        parts = bg.split("/", 1)
        return ( rgb_to_hex(hex_to_rgb(parts[0])), rgb_to_hex(hex_to_rgb(parts[1])) )
    if bg.startswith("//"):
        base = rgb_to_hex(hex_to_rgb(bg[2:]))
        return (color_luminance(base, 0.35), color_luminance(base, -0.15))
    base = rgb_to_hex(hex_to_rgb(bg))
    return (base, base)


# ---- Optical & Font Metrics ------------------------------------------------

class RichCanvas:
    """Wraps a PIL RGBA Image with Quotly optical trim insets {t, b}."""
    def __init__(self, image: Image.Image, opt_t: int = 0, opt_b: int = 0, pill: bool = False):
        self.image = image
        self.width = image.width
        self.height = image.height
        self.optical = {"t": max(0, int(round(opt_t))), "b": max(0, int(round(opt_b)))}
        self.pill = pill
        self._tail_offset = 0


_METRICS_CACHE = {}
_CAP_CACHE = {}
_MEASURE_DR = ImageDraw.Draw(Image.new("RGBA", (1, 1)))


def font_metrics(font_size: int) -> tuple[int, int]:
    font_size = int(round(font_size))
    if font_size in _METRICS_CACHE:
        return _METRICS_CACHE[font_size]
    f = L.style_font(font_size)
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
    f = L.style_font(font_size, bold=bold)
    try:
        bbox = _MEASURE_DR.textbbox((0, 0), "H", font=f, anchor="ls")
        cap = abs(bbox[1]) if bbox[1] < 0 else int(round(font_size * 0.714))
    except Exception:
        cap = int(round(font_size * 0.714))
    _CAP_CACHE[key] = cap
    return cap


def max_text_height(font_size: int, lines: int) -> float:
    asc, _ = font_metrics(font_size)
    return asc + (lines - 0.5) * (font_size * 1.2)


# ---- Vector & Shape Primitives (Supersampled 2x for crisp anti-aliasing) ---

def _cubic_bezier(p0, p1, p2, p3, steps=24):
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = (mt**3)*p0[0] + 3*(mt**2)*t*p1[0] + 3*mt*(t**2)*p2[0] + (t**3)*p3[0]
        y = (mt**3)*p0[1] + 3*(mt**2)*t*p1[1] + 3*mt*(t**2)*p2[1] + (t**3)*p3[1]
        pts.append((x, y))
    return pts


def _quad_bezier(p0, p1, p2, steps=16):
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = (mt**2)*p0[0] + 2*mt*t*p1[0] + (t**2)*p2[0]
        y = (mt**2)*p0[1] + 2*mt*t*p1[1] + (t**2)*p2[1]
        pts.append((x, y))
    return pts


def _arc_pts(cx, cy, r, start_deg, end_deg, steps=16):
    if r <= 0:
        return [(cx, cy)]
    pts = []
    for i in range(steps + 1):
        ang = math.radians(start_deg + (end_deg - start_deg) * (i / steps))
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def _bubble_polygon(w: float, h: float, radii: dict, tail_size: float, ox: float = 0.0, oy: float = 0.0):
    cap = lambda v: min(float(v), w / 2.0, h / 2.0)
    tl = cap(radii.get("tl", 0))
    tr = cap(radii.get("tr", 0))
    br = cap(radii.get("br", 0))
    bl = cap(radii.get("bl", 0))

    pts = []
    # Top-left to top-right
    pts.extend(_arc_pts(ox + w - tr, oy + tr, tr, -90, 0))
    # Bottom-right
    pts.extend(_arc_pts(ox + w - br, oy + h - br, br, 0, 90))

    if tail_size > 0:
        t = float(tail_size)
        # Bottom edge continues flat to the left past bubble edge
        pts.append((ox - t, oy + h))
        # Smooth cubic bezier up to the left edge
        pts.extend(
            _cubic_bezier(
                (ox - t, oy + h),
                (ox - t * 0.4, oy + h),
                (ox, oy + h - bl * 0.3),
                (ox, oy + h - bl),
                steps=24,
            )
        )
    else:
        pts.extend(_arc_pts(ox + bl, oy + h - bl, bl, 90, 180))

    # Top-left
    pts.extend(_arc_pts(ox + tl, oy + tl, tl, 180, 270))
    return pts


def _diagonal_gradient(w: int, h: int, c1: tuple, c2: tuple) -> Image.Image:
    w, h = max(1, w), max(1, h)
    if c1[:3] == c2[:3]:
        return Image.new("RGBA", (w, h), (*c1[:3], 255))
    small = Image.new("RGBA", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126.0
            r = int(round(c1[0] + (c2[0] - c1[0]) * t))
            g = int(round(c1[1] + (c2[1] - c1[1]) * t))
            b = int(round(c1[2] + (c2[2] - c1[2]) * t))
            px[x, y] = (r, g, b, 255)
    return small.resize((w, h), Image.BICUBIC)


def draw_bubble_shape(
    color_one: str,
    color_two: str,
    w: int,
    h: int,
    radii: dict | float,
    tail_size: float = 0,
    glass_lw: float = 0,
) -> RichCanvas:
    w, h = max(1, int(math.ceil(w))), max(1, int(math.ceil(h)))
    if isinstance(radii, (int, float)):
        radii = {"tl": radii, "tr": radii, "br": radii, "bl": radii}
    extra_left = int(math.ceil(tail_size * 0.8)) if tail_size > 0 else 0
    total_w = w + extra_left

    ss = 2
    sw, sh = total_w * ss, h * ss
    s_radii = {k: v * ss for k, v in radii.items()}
    poly = _bubble_polygon(w * ss, h * ss, s_radii, tail_size * ss, ox=extra_left * ss, oy=0)

    mask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    mask_1x = mask.resize((total_w, h), Image.LANCZOS)

    fill_img = _diagonal_gradient(total_w, h, hex_to_rgb(color_one), hex_to_rgb(color_two))
    out = Image.new("RGBA", (total_w, h), (0, 0, 0, 0))
    out.paste(fill_img, (0, 0), mask_1x)

    if glass_lw > 0:
        stroke_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        sdr = ImageDraw.Draw(stroke_layer)
        pts_closed = poly + [poly[0]]
        # Uniform inner hairline border (alpha 0.09)
        sdr.line(pts_closed, fill=(255, 255, 255, int(round(0.09 * 255))), width=max(1, int(round(glass_lw * 2 * ss))))

        # Top edge highlight fading out by 40% height (alpha 0.22 -> 0)
        top_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        tdr = ImageDraw.Draw(top_layer)
        tdr.line(pts_closed, fill=(255, 255, 255, int(round(0.22 * 255))), width=max(1, int(round(glass_lw * 2.6 * ss))))

        fade_h = max(1, int(round(sh * 0.4)))
        fade_mask = Image.new("L", (1, sh), 0)
        for y in range(fade_h):
            fade_mask.putpixel((0, y), int(round(255 * (1.0 - y / fade_h))))
        fade_mask = fade_mask.resize((sw, sh), Image.NEAREST)
        top_layer.putalpha(ImageChops.multiply(top_layer.split()[3], fade_mask))

        stroke_layer = Image.alpha_composite(stroke_layer, top_layer)
        stroke_layer.putalpha(ImageChops.multiply(stroke_layer.split()[3], mask))
        stroke_1x = stroke_layer.resize((total_w, h), Image.LANCZOS)
        out = Image.alpha_composite(out, stroke_1x)

    rc = RichCanvas(out, 0, 0)
    rc._tail_offset = extra_left
    return rc


def round_image(img: Image.Image, radii: dict | float) -> Image.Image:
    w, h = img.size
    if isinstance(radii, (int, float)):
        radii = {"tl": radii, "tr": radii, "br": radii, "bl": radii}
    ss = 2
    s_radii = {k: v * ss for k, v in radii.items()}
    poly = _bubble_polygon(w * ss, h * ss, s_radii, 0, 0, 0)
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def draw_quote_icon(size: int, color: str, alpha: float = 1.0) -> Image.Image:
    size = max(4, int(round(size)))
    ss = 2
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
        tail_pts = [p0] + _quad_bezier(p0, c1, p1) + [p2] + _quad_bezier(p2, c2, p3)
        dr.polygon(tail_pts, fill=fill)

    return im.resize((size, size), Image.LANCZOS)


def gradient_tint(rc: RichCanvas, color_from: str, color_to: str) -> RichCanvas:
    if not rc or rc.width < 2:
        return rc
    w, h = rc.width, rc.height
    c1, c2 = hex_to_rgb(color_from), hex_to_rgb(color_to)
    grad = Image.new("RGBA", (w, 1))
    for x in range(w):
        t = x / max(1, w - 1)
        r = int(round(c1[0] + (c2[0] - c1[0]) * t))
        g = int(round(c1[1] + (c2[1] - c1[1]) * t))
        b = int(round(c1[2] + (c2[2] - c1[2]) * t))
        grad.putpixel((x, 0), (r, g, b, 255))
    grad = grad.resize((w, h), Image.NEAREST)
    alpha = rc.image.split()[3]
    grad.putalpha(alpha)
    return RichCanvas(grad, rc.optical["t"], rc.optical["b"], pill=rc.pill)


# ---- Avatar Renderer -------------------------------------------------------

def _extract_initials(name: str) -> str:
    clean_name = qe.clean(name or "").strip()
    if not clean_name:
        return "?"
    words = [w for w in clean_name.upper().split() if any(c.isalnum() for c in w)]
    if not words:
        return "?"
    first = next(ch for ch in words[0] if ch.isalnum())
    if len(words) > 1:
        last = next(ch for ch in words[-1] if ch.isalnum())
        return first + last
    return first


def draw_avatar(avatar_bytes: bytes | None, name: str, user_id: int, size: int) -> Image.Image:
    size = max(16, int(round(size)))
    ss = 2
    s_size = size * ss
    mask = Image.new("L", (s_size, s_size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s_size - 1, s_size - 1], fill=255)
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

    # Fallback: Quotly gradient circle + initials
    idx = abs(int(user_id or 0)) % 7
    c1, c2 = AVATAR_COLORS[idx]
    bg = _diagonal_gradient(size, size, hex_to_rgb(c1), hex_to_rgb(c2))
    dr = ImageDraw.Draw(bg)
    letters = _extract_initials(name)
    f_size = int(round(size * (0.38 if len(letters) > 1 else 0.48)))
    f = qf.pick(L.style_font(f_size, bold=True), letters[0])
    dr.text((size / 2.0, size / 2.0), letters, font=f, fill=(255, 255, 255, 255), anchor="mm")

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    return out


# ---- Single-line Label & Role Tag ------------------------------------------

def draw_label(text: str, font_size: int, color: str, bold: bool = False, alpha: float = 1.0) -> RichCanvas:
    font_size = int(round(font_size))
    text = qe.clean(str(text or ""))
    asc, desc = font_metrics(font_size)
    f = L.style_font(font_size, bold=bold)
    w = max(1, int(math.ceil(qf.tw(text, f))))
    h = max(1, int(math.ceil(asc + desc)))
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    qf.draw_baseline(dr, 0, asc, text, f, rgba_tuple(color, alpha))
    opt_t = asc - cap_height(font_size, bold)
    opt_b = h - asc
    return RichCanvas(im, opt_t, opt_b)


def draw_tag(tag_tuple: tuple[str, str | None], s_fn, text_color: str) -> RichCanvas | None:
    if not tag_tuple:
        return None
    role, custom_title = tag_tuple[0], tag_tuple[1] if len(tag_tuple) > 1 else None
    label_text = custom_title if custom_title else role
    if not label_text:
        return None

    size = s_fn(15)
    colors = TAG_COLORS.get(role)
    if not colors:
        return draw_label(label_text, size, text_color, alpha=0.65)

    color = colors["light"] if text_color == "#000" else colors["dark"]
    lbl = draw_label(label_text, size, color)
    opt = lbl.optical
    pad_x = s_fn(8)
    pad_y = s_fn(4)
    w = int(math.ceil(lbl.width + 2 * pad_x))
    h = int(math.ceil(lbl.height - opt["t"] - opt["b"] + 2 * pad_y))

    pill_bg = draw_bubble_shape(color, color, w, h, h / 2.0, 0, 0).image
    # Apply 0.15 tint alpha
    r, g, b, a = pill_bg.split()
    a = a.point(lambda p: int(round(p * 0.15)))
    pill_bg.putalpha(a)

    pill_bg.alpha_composite(lbl.image, (int(round(pad_x)), int(round(pad_y - opt["t"]))))
    return RichCanvas(pill_bg, 0, 0, pill=True)


# ---- Entity-Aware Text Engine (Prepare -> ShrinkWrap -> Render) ------------

def _utf16_map(text: str) -> list[int]:
    """Map UTF-16 code unit offset -> Python character index."""
    mapping = []
    for idx, ch in enumerate(text):
        mapping.append(idx)
        if ord(ch) > 0xFFFF:
            mapping.append(idx)
    mapping.append(len(text))
    return mapping


def split_by_blockquotes(text: str, entities: list[dict] | None) -> list[dict] | None:
    if not entities:
        return None
    quotes = [
        e for e in entities
        if isinstance(e, dict) and e.get("type") in ("blockquote", "expandable_blockquote")
    ]
    if not quotes:
        return None
    quotes.sort(key=lambda e: e.get("offset", 0))
    u16_len = sum(2 if ord(c) > 0xFFFF else 1 for c in text)
    u_map = _utf16_map(text)

    def slice_ents(u_start: int, u_end: int):
        out = []
        for e in entities:
            if not isinstance(e, dict) or e.get("type") in ("blockquote", "expandable_blockquote"):
                continue
            eo = e.get("offset", 0)
            el = e.get("length", 0)
            if eo < u_end and eo + el > u_start:
                f = max(eo, u_start)
                t = min(eo + el, u_end)
                out.append({**e, "offset": f - u_start, "length": t - f})
        return out

    parts = []
    pos = 0
    for q in quotes:
        qo = max(0, min(u16_len, q.get("offset", 0)))
        ql = max(0, min(u16_len - qo, q.get("length", 0)))
        if qo < pos:
            continue
        if qo > pos:
            sub = text[u_map[pos]:u_map[qo]].rstrip("\n")
            if sub:
                parts.append({"text": sub, "entities": slice_ents(pos, qo), "quote": False})
        q_sub = text[u_map[qo]:u_map[qo + ql]]
        parts.append({"text": q_sub, "entities": slice_ents(qo, qo + ql), "quote": True})
        pos = qo + ql

    if pos < u16_len:
        tail = text[u_map[pos]:].lstrip("\n")
        if tail:
            parts.append({"text": tail, "entities": slice_ents(pos, u16_len), "quote": False})

    return parts if parts else None


def prepare_text(text: str, entities: list[dict] | str | None, font_size: int) -> dict:
    font_size = int(round(font_size))
    asc, desc = font_metrics(font_size)
    line_height = font_size * 1.2
    emoji_size = font_size * EMOJI_SCALE

    if not text:
        return {
            "segments": [],
            "fontSize": font_size,
            "lineHeight": line_height,
            "emojiSize": emoji_size,
            "ascent": asc,
            "descent": desc,
        }

    text = text.replace("і", "i")
    n_chars = len(text)
    char_styles = [set() for _ in range(n_chars)]

    if isinstance(entities, str):
        for s in char_styles:
            s.add(entities)
    elif isinstance(entities, list):
        u_map = _utf16_map(text)
        max_u = len(u_map) - 1
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            etype = ent.get("type", "")
            if etype in ENTITY_MONOSPACE:
                st = "monospace"
            elif etype in ENTITY_MENTION:
                st = "mention"
            else:
                st = etype
            o = max(0, min(max_u, int(ent.get("offset", 0))))
            l = max(0, min(max_u - o, int(ent.get("length", 0))))
            c_start, c_end = u_map[o], u_map[o + l]
            for i in range(c_start, c_end):
                char_styles[i].add(st)

    # Clean fancy letters on a per-character basis so offsets stay 1:1
    cleaned_chars = [qe.clean(ch) for ch in text]
    clean_str = "".join(cleaned_chars)

    # Map emojis
    emoji_spans = [None] * len(clean_str)
    emojis_found = []
    for m in qe.EMOJI_RE.finditer(clean_str):
        e_idx = len(emojis_found)
        e_str = m.group()
        emojis_found.append(e_str)
        for i in range(m.start(), m.end()):
            if i < len(emoji_spans):
                emoji_spans[i] = (e_idx, e_str)

    # Expand char_styles if clean_str length changed slightly from NFKC
    while len(char_styles) < len(clean_str):
        char_styles.append(set(char_styles[-1]) if char_styles else set())

    # Tokenize into word/space/break/emoji runs
    raw_segments = []
    for m in re.finditer(r"\r\n|\r|\n|[^\S\r\n]+|\S+", clean_str):
        start, end = m.start(), m.end()
        sub_start = start
        for i in range(start, end):
            ch = clean_str[i]
            is_brk = ch in ("\r", "\n")
            prev_i = i - 1 if i > sub_start else None
            needs_split = is_brk
            if prev_i is not None and not needs_split:
                if emoji_spans[i] != emoji_spans[prev_i]:
                    needs_split = True
                elif char_styles[i] != char_styles[prev_i]:
                    needs_split = True
            if needs_split:
                if i > sub_start:
                    _push_seg(raw_segments, clean_str, char_styles, emoji_spans, sub_start, i, font_size, emoji_size)
                sub_start = i
        if sub_start < end:
            _push_seg(raw_segments, clean_str, char_styles, emoji_spans, sub_start, end, font_size, emoji_size)

    # Split CJK characters for per-character wrapping
    cjk_split = []
    for seg in raw_segments:
        if seg["kind"] != "text" or not CJK_RE.search(seg["text"]) or len(seg["text"]) <= 1:
            cjk_split.append(seg)
            continue
        for ch in seg["text"]:
            cjk_split.append({
                **seg,
                "text": ch,
                "width": qf.tw(ch, seg["font"]),
            })

    # Merge left-sticky punctuation
    merged = []
    for seg in cjk_split:
        if (
            seg["kind"] == "text"
            and len(seg["text"]) == 1
            and seg["text"] in LEFT_STICKY_PUNCT
            and merged
            and merged[-1]["kind"] == "text"
            and merged[-1]["styles"] == seg["styles"]
        ):
            merged[-1]["text"] += seg["text"]
            merged[-1]["width"] = qf.tw(merged[-1]["text"], merged[-1]["font"])
        else:
            merged.append(seg)

    return {
        "segments": merged,
        "fontSize": font_size,
        "lineHeight": line_height,
        "emojiSize": emoji_size,
        "ascent": asc,
        "descent": desc,
    }


def _push_seg(out: list, text: str, styles_list: list, emoji_spans: list, start: int, end: int, font_size: int, emoji_size: float):
    sub = text[start:end]
    st = sorted(styles_list[start])
    em_info = emoji_spans[start]

    if sub in ("\r", "\n", "\r\n"):
        kind = "break"
    elif sub.isspace():
        kind = "space"
    elif em_info is not None:
        kind = "emoji"
    else:
        kind = "text"

    f = L.style_font(
        font_size,
        bold=("bold" in st),
        italic=("italic" in st),
        mono=("monospace" in st),
    )

    em_img = None
    width = 0.0
    if kind == "emoji":
        em_img = qe.fetch(em_info[1])
        width = emoji_size
    elif kind != "break":
        width = qf.tw(sub, f)

    out.append({
        "text": sub,
        "kind": kind,
        "styles": st,
        "font": f,
        "emojiImage": em_img,
        "width": width,
    })


def _line_direction(segments: list, start_idx: int) -> str:
    for i in range(start_idx, len(segments)):
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
    f = last_seg["font"]
    ell_w = qf.tw("…", f)
    target_w = max_width - ell_w

    total_w = 0.0
    trim_idx = len(line["segments"])
    for idx, lseg in enumerate(line["segments"]):
        if total_w + lseg["width"] > target_w:
            trim_idx = idx
            break
        total_w += lseg["width"]

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
    cur_line = {"segments": [], "width": 0.0, "direction": _line_direction(segments, 0), "truncated": False}
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
                _apply_truncation(cur_line, prepared, max_width)
                break
            if cur_line["width"] > max_line_w:
                max_line_w = cur_line["width"]
            cur_line["y"] = line_y
            lines.append(cur_line)
            line_y += line_height
            pending_break = None
            cur_line = {"segments": [], "width": 0.0, "direction": _line_direction(segments, i + 1), "truncated": False}
            i += 1
            continue

        seg_w = emoji_size if seg["kind"] == "emoji" else seg["width"]

        # Hang trailing spaces
        if seg["kind"] == "space" and cur_line["width"] + seg_w > max_width:
            cur_line["segments"].append({"index": i, "width": seg_w})
            i += 1
            continue

        # Overwide single word on empty line -> character split
        if not cur_line["segments"] and seg_w > max_width and seg["kind"] == "text" and len(seg["text"]) > 1:
            chars = list(seg["text"])
            c_widths = [qf.tw(c, seg["font"]) for c in chars]
            cursor = 0
            while cursor < len(chars):
                fit_cnt, fit_w = 0, 0.0
                for g in range(cursor, len(chars)):
                    if fit_w + c_widths[g] > max_width and fit_cnt > 0:
                        break
                    fit_w += c_widths[g]
                    fit_cnt += 1
                if fit_cnt == 0:
                    fit_cnt = 1
                    fit_w = c_widths[cursor]
                is_last = (cursor + fit_cnt) >= len(chars)
                cur_line["segments"].append({
                    "index": i,
                    "width": fit_w,
                    "c_start": cursor if cursor > 0 else None,
                    "c_end": None if is_last else (cursor + fit_cnt),
                })
                cur_line["width"] += fit_w
                if not is_last:
                    if max_height > 0 and (line_y + line_height > max_height):
                        truncated = True
                        _apply_truncation(cur_line, prepared, max_width)
                        break
                    if cur_line["width"] > max_line_w:
                        max_line_w = cur_line["width"]
                    cur_line["y"] = line_y
                    lines.append(cur_line)
                    line_y += line_height
                    cur_line = {"segments": [], "width": 0.0, "direction": cur_line["direction"], "truncated": False}
                cursor += fit_cnt
            if truncated:
                break
            i += 1
            continue

        # Overflow check
        if cur_line["width"] + seg_w > max_width and cur_line["segments"]:
            if max_height > 0 and (line_y + line_height > max_height):
                truncated = True
                _apply_truncation(cur_line, prepared, max_width)
                break

            if pending_break is not None:
                b_idx, b_w, b_cnt = pending_break
                cur_line["segments"] = cur_line["segments"][:b_cnt]
                cur_line["width"] = b_w
                if cur_line["width"] > max_line_w:
                    max_line_w = cur_line["width"]
                cur_line["y"] = line_y
                lines.append(cur_line)
                line_y += line_height
                pending_break = None
                cur_line = {"segments": [], "width": 0.0, "direction": _line_direction(segments, b_idx + 1), "truncated": False}
                i = b_idx + 1
                continue

            # Finalize current line and push current segment to next line
            if cur_line["width"] > max_line_w:
                max_line_w = cur_line["width"]
            cur_line["y"] = line_y
            lines.append(cur_line)
            line_y += line_height
            cur_line = {"segments": [], "width": 0.0, "direction": _line_direction(segments, i), "truncated": False}
            if seg["kind"] != "space":
                cur_line["segments"].append({"index": i, "width": seg_w})
                cur_line["width"] += seg_w
            pending_break = None
            i += 1
            continue

        if seg["kind"] == "space":
            pending_break = (i, cur_line["width"], len(cur_line["segments"]))

        cur_line["segments"].append({"index": i, "width": seg_w})
        cur_line["width"] += seg_w
        i += 1

    if cur_line["segments"]:
        if cur_line["width"] > max_line_w:
            max_line_w = cur_line["width"]
        cur_line["y"] = line_y
        lines.append(cur_line)

    for line in lines:
        x = 0.0
        content_w = 0.0
        for lseg in line["segments"]:
            lseg["x"] = x
            x += lseg["width"]
            src = segments[lseg["index"]]
            if src["kind"] != "space":
                content_w = x
        line["contentWidth"] = content_w or line["width"]

    total_h = (lines[-1]["y"] + descent) if lines else 0
    return {
        "lines": lines,
        "width": max_line_w,
        "height": total_h,
        "lineCount": len(lines),
        "truncated": truncated,
    }


def shrink_wrap(prepared: dict, max_width: float, max_height: float) -> dict:
    initial = layout_text(prepared, max_width, max_height)
    if initial["lineCount"] <= 1 or initial["truncated"]:
        return initial

    max_seg_w = max((s["width"] for s in prepared["segments"]), default=0.0)
    lo = max(max_seg_w, initial["width"] / initial["lineCount"] * 0.7)
    hi = initial["width"]
    target_lines = initial["lineCount"]

    while hi - lo > 2.0:
        mid = (lo + hi) / 2.0
        trial = layout_text(prepared, mid, max_height)
        if trial["lineCount"] <= target_lines and not trial["truncated"]:
            hi = mid
        else:
            lo = mid

    return layout_text(prepared, math.ceil(hi), max_height)


def render_text(layout_res: dict, prepared: dict, font_color: str) -> RichCanvas:
    lines = layout_res["lines"]
    width = layout_res["width"]
    height = layout_res["height"]
    segments = prepared["segments"]
    font_size = prepared["fontSize"]
    emoji_size = int(round(prepared["emojiSize"]))

    if not lines or width <= 0:
        return RichCanvas(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)

    cw = max(1, int(math.ceil(width)))
    ch = max(1, int(math.ceil(height)))
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    dr = ImageDraw.Draw(canvas)

    for line_idx, line in enumerate(lines):
        for lseg in line["segments"]:
            seg = segments[lseg["index"]]
            styles = seg["styles"]

            if "monospace" in styles:
                fill = rgba_tuple("#5887a7", 1.0)
            elif "mention" in styles:
                fill = rgba_tuple("#6ab7ec", 1.0)
            elif "spoiler" in styles:
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
                    qf.draw_baseline(dr, draw_x, draw_y, seg["text"], seg["font"], fill)
            elif seg["kind"] in ("space", "break"):
                pass
            else:
                txt = seg["text"]
                if lseg.get("c_start") is not None or lseg.get("c_end") is not None:
                    txt = txt[lseg.get("c_start") or 0 : lseg.get("c_end") or len(txt)]
                qf.draw_baseline(dr, draw_x, draw_y, txt, seg["font"], fill)
                tw_val = qf.tw(txt, seg["font"])

                if "strikethrough" in styles:
                    sy = draw_y - font_size / 2.8
                    sh = max(1, font_size * 0.1)
                    dr.rectangle([draw_x, sy, draw_x + tw_val, sy + sh], fill=fill)

                if "underline" in styles:
                    uy = draw_y + 2
                    uh = max(1, font_size * 0.1)
                    dr.rectangle([draw_x, uy, draw_x + tw_val, uy + uh], fill=fill)

        if line_idx == len(lines) - 1 and line.get("truncated"):
            last_lseg = line["segments"][-1] if line["segments"] else None
            if last_lseg:
                last_seg = segments[last_lseg["index"]]
                ell_x = last_lseg["x"] + last_lseg["width"]
                if line["direction"] == "rtl":
                    ew = qf.tw("…", last_seg["font"])
                    ell_x = width - last_lseg["x"] - last_lseg["width"] - ew
                qf.draw_baseline(dr, ell_x, line["y"], "…", last_seg["font"], rgba_tuple(font_color, 1.0))

    # Optical insets
    first_line = lines[0]
    last_line = lines[-1]
    any_bold = any(s["kind"] == "text" and "bold" in s["styles"] for s in segments)

    first_items = [
        segments[ls["index"]]
        for ls in first_line["segments"]
        if segments[ls["index"]]["kind"] not in ("space", "break")
    ]
    if first_items and all(s["kind"] == "emoji" for s in first_items):
        top_val = font_size * 0.85
    else:
        top_val = float(cap_height(font_size, any_bold))
        for s in first_items:
            if s["kind"] == "text" and TALL_SCRIPT_RE.search(s["text"]):
                try:
                    bbox = _MEASURE_DR.textbbox((0, 0), s["text"], font=s["font"], anchor="ls")
                    top_val = max(top_val, float(abs(bbox[1])))
                except Exception:
                    pass

    last_items = [segments[ls["index"]] for ls in last_line["segments"]]
    emoji_only_bottom = (
        (emoji_size - font_size * 0.85)
        if last_items and all(s["kind"] == "emoji" for s in last_items)
        else 0.0
    )

    opt_t = first_line["y"] - top_val
    opt_b = math.ceil(height) - last_line["y"] - emoji_only_bottom
    return RichCanvas(canvas, opt_t, opt_b)


def draw_multiline_text(
    text: str,
    entities: list[dict] | str | None,
    font_size: int,
    font_color: str,
    max_width: float,
    max_height: float,
) -> RichCanvas:
    prepared = prepare_text(text, entities, font_size)
    if not prepared["segments"]:
        return RichCanvas(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)
    lay = shrink_wrap(prepared, max_width, max_height)
    return render_text(lay, prepared, font_color)


# ---- Box-Model Layout Engine (Port of layout-box.js) -----------------------

def _norm_pad(pad) -> dict:
    if not pad:
        return {"t": 0, "r": 0, "b": 0, "l": 0}
    if isinstance(pad, (int, float)):
        return {"t": pad, "r": pad, "b": pad, "l": pad}
    return {
        "t": pad.get("t", 0),
        "r": pad.get("r", 0),
        "b": pad.get("b", 0),
        "l": pad.get("l", 0),
    }


def leaf(rc: RichCanvas | Image.Image | None, **opts) -> dict | None:
    if rc is None:
        return None
    if isinstance(rc, Image.Image):
        rc = RichCanvas(rc, 0, 0)
    if rc.width <= 1 and rc.height <= 1:
        return None
    w = opts.get("w", rc.width)
    max_w = opts.get("maxW")
    if max_w and w > max_w:
        w = max_w
    use_opt = opts.get("optical", True) and ("h" not in opts)
    trim_t = rc.optical["t"] if use_opt else 0
    trim_b = rc.optical["b"] if use_opt else 0
    raw_h = opts.get("h", rc.height)
    return {
        "kind": "leaf",
        "rc": rc,
        "trimT": trim_t,
        "role": opts.get("role", "block"),
        "w": w,
        "h": raw_h - trim_t - trim_b,
        "bleed": bool(opts.get("bleed", False)),
        "paint": opts.get("paint"),
    }


def box(**opts) -> dict:
    children = [c for c in (opts.get("children") or []) if c is not None]
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
        "children": children,
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
    pad = n["pad"]
    if n["dir"] == "col":
        outer_w = 0.0
        for i, c in enumerate(n["children"]):
            cw = c["w"] if c.get("bleed") else (c["w"] + pad["l"] + pad["r"])
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
        w += pad["l"] + pad["r"]

    n["w"] = int(math.ceil(w))
    n["h"] = int(math.ceil(h + pad["t"] + pad["b"]))
    if n["w"] < n["minW"]:
        n["w"] = int(math.ceil(n["minW"]))
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
    pad = n["pad"]
    inner_w = n["w"] - pad["l"] - pad["r"]
    if n["dir"] == "col":
        cy = y + pad["t"]
        for i, c in enumerate(n["children"]):
            cx = x + pad["l"]
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
                return y + pad["t"] + (n["h"] - pad["t"] - pad["b"] - c["h"]) / 2.0
            if n["align"] == "end":
                return y + n["h"] - pad["b"] - c["h"]
            return y + pad["t"]

        gap_val = n["gap"] if not callable(n["gap"]) else 0
        if n["justify"] == "between" and len(n["children"]) == 2:
            a, b = n["children"]
            avail_a = inner_w - b["w"] - gap_val
            if a["w"] > avail_a:
                a["w"] = max(0, int(round(avail_a)))
            place(a, x + pad["l"], cross_y(a))
            place(b, x + n["w"] - pad["r"] - b["w"], cross_y(b))
            return

        cx = x + pad["l"]
        for c in n["children"]:
            place(c, cx, cross_y(c))
            cx += c["w"] + gap_val


def _fade_overflow(leaf_node: dict) -> Image.Image:
    im = leaf_node["rc"].image
    w = max(1, int(round(leaf_node["w"])))
    h = im.height
    alpha = im.split()[3].load()
    cut = min(w, im.width)
    for x in range(cut - 1, int(cut * 0.4), -1):
        ink = any(alpha[x, y] > 8 for y in range(h))
        if not ink:
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
            im = n["rc"].image
            if im.width > n["w"] + 1:
                im = _fade_overflow(n)
            canvas.alpha_composite(im, (int(round(n["x"])), int(round(n["y"] - n["trimT"]))))
    else:
        for c in n["children"]:
            render_tree(canvas, c)
    if n.get("fg"):
        n["fg"](canvas, n)


# ---- Composer (Accent Block, Drop Shadow, Single Message Bubble) -----------

def _accent_block(s_fn, accent_color: str, children: list, icon: bool = False) -> dict:
    pad_y = s_fn(8)
    pad_l = s_fn(12)
    pad_r = s_fn(32 if icon else 12)
    bar_w = int(math.ceil(s_fn(4)))
    radius = s_fn(9)  # concentric(25, 16) = 9

    def paint_bg(canvas: Image.Image, n: dict):
        bw, bh = int(math.ceil(n["w"])), int(math.ceil(n["h"]))
        solid = draw_bubble_shape(accent_color, accent_color, bw, bh, radius, 0, 0).image
        # Tinted backdrop (0.10 alpha)
        tinted = solid.copy()
        a = tinted.split()[3].point(lambda p: int(round(p * 0.10)))
        tinted.putalpha(a)
        canvas.alpha_composite(tinted, (n["x"], n["y"]))

        # Solid left bar clipped to the block's rounded left corners
        bar_slice = solid.crop((0, 0, min(bar_w, bw), bh))
        canvas.alpha_composite(bar_slice, (n["x"], n["y"]))

        if icon:
            q_icon = draw_quote_icon(s_fn(16), accent_color, 1.0)
            ix = int(round(n["x"] + bw - s_fn(16) - s_fn(4)))
            iy = int(round(n["y"] + s_fn(4)))
            canvas.alpha_composite(q_icon, (ix, iy))

    return box(
        dir="col",
        gap=s_fn(8),
        pad={"t": pad_y, "r": pad_r, "b": pad_y, "l": pad_l},
        bg=paint_bg,
        children=children,
    )


def _paste_with_shadow(dest: Image.Image, src: Image.Image, x: int, y: int, s_fn):
    blur = s_fn(6)
    off_y = s_fn(2)
    pad = int(math.ceil(blur * 2))
    alpha = src.split()[3]
    sh_alpha = Image.new("L", (src.width + 2 * pad, src.height + 2 * pad), 0)
    sh_alpha.paste(alpha, (pad, pad))
    sh_alpha = sh_alpha.filter(ImageFilter.GaussianBlur(blur / 2.0))
    sh_layer = Image.new("RGBA", sh_alpha.size, (0, 0, 0, int(round(0.24 * 255))))
    sh_layer.putalpha(sh_alpha.point(lambda p: int(round(p * 0.24))))

    sx, sy = int(round(x - pad)), int(round(y - pad + off_y))
    # Safe alpha composite onto dest
    tmp = Image.new("RGBA", dest.size, (0, 0, 0, 0))
    tmp.paste(sh_layer, (sx, sy))
    dest.alpha_composite(tmp)
    dest.alpha_composite(src, (int(round(x)), int(round(y))))


def _render_single_message(
    msg: dict,
    bg_one: str,
    bg_two: str,
    scale: int,
    show_name: bool,
    show_avatar: bool,
    group_pos: str,
) -> Image.Image | None:
    s = lambda v: v * scale
    width_budget = 512 * scale
    height_budget = 512 * scale
    max_lines = 16

    back_style = light_or_dark(bg_one)
    palette = NAME_COLORS_LIGHT if back_style == "light" else NAME_COLORS_DARK
    text_color = "#000000" if back_style == "light" else "#ffffff"

    uid = int(msg.get("user_id") or 0)
    name_color = palette[abs(uid) % 7]

    contrast = get_contrast_ratio(color_luminance(bg_one, 0.55), name_color)
    if contrast > 90 or contrast < 30:
        name_color = adjust_contrast(color_luminance(bg_two, 0.55), name_color)

    # 1. Sender Name & Role Tag
    name_rc = None
    raw_name = str(msg.get("name") or "User").strip() or "User"
    if show_name:
        name_size = s(20)
        name_rc = draw_multiline_text(raw_name, "bold", name_size, name_color, width_budget, name_size)
        has_emoji = bool(qe.EMOJI_RE.search(raw_name))
        if not has_emoji and name_rc.width > 1:
            name_rc = gradient_tint(name_rc, name_color, color_luminance(name_color, 0.25))

    header_node = None
    if name_rc:
        tag_rc = draw_tag(msg.get("tag"), s, text_color) if msg.get("tag") else None
        tag_leaf = leaf(tag_rc, role="text") if tag_rc else None
        name_max = s(300) - (tag_leaf["w"] + s(8) if tag_leaf else 0)
        name_leaf = leaf(name_rc, role="text", maxW=max(s(40), name_max))
        if tag_leaf:
            header_node = box(
                dir="row",
                justify="between",
                align="center" if tag_rc.pill else "end",
                role="text",
                gap=s(8),
                stretch=True,
                children=[name_leaf, tag_leaf],
            )
        else:
            header_node = name_leaf

    # 2. Reply Block
    reply_node = None
    rep = msg.get("reply")
    if isinstance(rep, dict) and rep.get("name") and rep.get("text"):
        r_uid = int(rep.get("user_id") or abs(hash(rep.get("name"))) or 0)
        r_color = palette[abs(r_uid) % 7]
        r_name_rc = draw_multiline_text(str(rep["name"]), "bold", s(19), r_color, width_budget * 0.9, s(19))
        r_text_rc = draw_multiline_text(
            str(rep["text"]),
            rep.get("entities") or [],
            s(19),
            text_color,
            width_budget * 0.9,
            s(19),
        )
        r_inner = box(
            dir="col",
            gap=s(8),
            children=[leaf(r_name_rc, role="text"), leaf(r_text_rc, role="text")],
        )
        reply_node = _accent_block(s, r_color, [r_inner], icon=False)

    # 3. Optional Media (Photo / Sticker inside bubble)
    media_bytes = msg.get("media")
    media_type = msg.get("media_type", "photo")
    media_img = None
    if media_bytes:
        try:
            media_img = Image.open(io.BytesIO(media_bytes)).convert("RGBA")
        except Exception:
            media_img = None

    # 4. Main Message Text (and Blockquote splitting)
    raw_text = str(msg.get("text") or "")
    entities = msg.get("entities") or []
    font_size = s(24)

    # Big Emoji check (1-3 standalone emojis with no reply/media/cards)
    big_emoji_rc = None
    if raw_text.strip() and not media_img and not reply_node:
        stripped = re.sub(r"\s+", "", raw_text)
        found_em = [m.group() for m in qe.EMOJI_RE.finditer(stripped)]
        if 1 <= len(found_em) <= 3 and "".join(found_em) == stripped:
            big_emoji_rc = draw_multiline_text(
                raw_text.strip(), [], font_size * 4, text_color, width_budget, font_size * 5
            )
            media_img = big_emoji_rc.image
            media_type = "sticker"

    text_rc = None
    text_blocks = None
    if raw_text and not big_emoji_rc:
        parts = split_by_blockquotes(raw_text, entities)
        if parts:
            text_blocks = []
            lines_left = max_lines
            for p in parts:
                if lines_left <= 0:
                    break
                rc = draw_multiline_text(
                    p["text"],
                    p["entities"],
                    font_size,
                    text_color,
                    width_budget,
                    min(height_budget - font_size, max_text_height(font_size, lines_left)),
                )
                text_blocks.append({"rc": rc, "quote": p["quote"]})
                asc, desc = font_metrics(font_size)
                used = round((rc.height - asc - desc) / (font_size * 1.2)) + 1 if rc.width > 1 else 0
                lines_left -= max(1, used)
            text_rc = text_blocks[0]["rc"] if text_blocks else None
        else:
            text_rc = draw_multiline_text(
                raw_text,
                entities,
                font_size,
                text_color,
                width_budget,
                min(height_budget - font_size, max_text_height(font_size, max_lines)),
            )

    if not text_rc and not header_node and not media_img and not reply_node:
        return None

    is_sticker = media_type == "sticker"
    if is_sticker:
        header_node = None

    stack_gap = lambda a, b: s(12)

    # Media node
    R = s(25)
    r_small = s(7)
    radii = {
        "tl": r_small if group_pos in ("middle", "last") else R,
        "tr": R,
        "br": R,
        "bl": r_small if group_pos in ("first", "middle") else R,
    }

    has_caption = bool(text_rc and text_rc.width > 1) or bool(text_blocks)
    media_only = bool(media_img) and not header_node and not has_caption and not reply_node
    flushable = bool(media_img) and not media_only and not is_sticker
    flush_top = flushable and not header_node and not reply_node
    flush_bottom = flushable and not has_caption

    media_node = None
    if media_img:
        max_m = max_m_size = (
            max(media_img.width, media_img.height)
            if big_emoji_rc
            else max(width_budget * 2.0 / 3.0, text_rc.width if text_rc else 0)
        )
        mw = media_img.width * (max_m / max(1, media_img.height))
        mh = max_m
        if mw >= max_m:
            mw = max_m
            mh = media_img.height * (max_m / max(1, media_img.width))

        mr = s(4)
        m_radii = (
            s(25 * 0.6)
            if (media_only or is_sticker)
            else {
                "tl": radii["tl"] if flush_top else mr,
                "tr": radii["tr"] if flush_top else mr,
                "br": radii["br"] if flush_bottom else mr,
                "bl": radii["bl"] if flush_bottom else mr,
            }
        )

        def paint_media(canvas: Image.Image, n: dict):
            tw, th = max(1, int(round(n["w"]))), max(1, int(round(n["h"])))
            resized = media_img.resize((tw, th), Image.LANCZOS)
            rounded = round_image(resized, m_radii)
            canvas.alpha_composite(rounded, (n["x"], n["y"]))

        media_node = leaf(media_img, optical=False, bleed=True, w=mw, h=mh, paint=paint_media)

    text_node = None
    if text_blocks:
        parts_nodes = [
            _accent_block(s, name_color, [leaf(b["rc"])], icon=True)
            if b["quote"]
            else leaf(b["rc"], role="text")
            for b in text_blocks
        ]
        text_node = box(dir="col", role="text", gap=stack_gap, children=parts_nodes)
    elif text_rc and text_rc.width > 1:
        text_node = leaf(text_rc, role="text")

    if media_node and flushable:
        inner = 0
        for nd in (header_node, reply_node, text_node):
            if nd:
                measure(nd)
                inner = max(inner, nd["w"])
        need = inner + 2 * s(16)
        if media_node["w"] < need <= media_node["w"] * 1.25:
            media_node["h"] *= need / media_node["w"]
            media_node["w"] = need

    bubble_pad = {
        "t": 0 if flush_top else s(16),
        "r": s(16),
        "b": 0 if flush_bottom else s(16),
        "l": s(16),
    }
    tail_size = s(14) if show_avatar else 0

    def paint_bubble_bg(canvas: Image.Image, n: dict):
        rect = draw_bubble_shape(bg_one, bg_two, n["w"], n["h"], radii, tail_size, s(2))
        _paste_with_shadow(canvas, rect.image, n["x"] - rect._tail_offset, n["y"], s)

    if is_sticker:
        chip = None
        if reply_node:
            def paint_chip_bg(canvas: Image.Image, n: dict):
                rect = draw_bubble_shape(bg_one, bg_two, n["w"], n["h"], s(17), 0, s(2))
                _paste_with_shadow(canvas, rect.image, n["x"], n["y"], s)

            chip = box(pad=s(8), bg=paint_chip_bg, children=[reply_node])
        root = box(dir="col", gap=stack_gap, children=[chip, media_node])
    else:
        root = box(
            dir="col",
            gap=stack_gap,
            pad=0 if media_only else bubble_pad,
            minW=0 if media_only else s(100),
            bg=paint_bubble_bg,
            children=[header_node, reply_node, media_node, text_node],
        )

    measure(root)

    shadow_pad = s(12)
    shadow_pad_top = s(4)
    bubble_x = s(50) + s(10)
    total_w = int(math.ceil(bubble_x + root["w"] + shadow_pad))
    avatar_h = (s(50) + s(2)) if show_avatar else 0
    total_h = int(math.ceil(shadow_pad_top + max(root["h"], avatar_h) + shadow_pad))

    place(root, bubble_x, shadow_pad_top)

    out_canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    render_tree(out_canvas, root)

    if show_avatar:
        av_img = draw_avatar(msg.get("avatar"), raw_name, uid, s(50))
        av_y = max(0, total_h - shadow_pad - s(50) - s(2))
        out_canvas.alpha_composite(av_img, (0, int(round(av_y))))

    return out_canvas


# ---- Public Interface Contract ---------------------------------------------

def render_messages(messages: list[dict], bg: str | None = None) -> Image.Image:
    """Render one or more Telegram messages into a Quotly-style 512px RGBA sticker."""
    try:
        if not messages or not isinstance(messages, list):
            return Image.new("RGBA", (512, 128), (0, 0, 0, 0))

        bg_one, bg_two = parse_bg_color(bg)
        scale = SCALE
        rendered = []

        n = len(messages)
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            uid = msg.get("user_id")
            prev_uid = messages[idx - 1].get("user_id") if idx > 0 and isinstance(messages[idx - 1], dict) else None
            next_uid = messages[idx + 1].get("user_id") if idx + 1 < n and isinstance(messages[idx + 1], dict) else None

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

            im = _render_single_message(msg, bg_one, bg_two, scale, show_name, show_avatar, group_pos)
            if im is not None:
                rendered.append((im, same_prev))

        if not rendered:
            # Sensible fallback bubble if empty input passed
            fallback = _render_single_message(
                {"name": "User", "user_id": 0, "text": "…", "entities": [], "avatar": None, "tag": None, "reply": None},
                bg_one, bg_two, scale, True, True, "single",
            )
            rendered = [(fallback, False)]

        # Stack messages using Quotly's margin overlap math
        if len(rendered) == 1:
            combined = rendered[0][0]
        else:
            overlap = (12 + 4) * scale  # shadowPad + shadowPadTop
            gap_same = 4 * scale
            gap_diff = 8 * scale
            max_w = max(im.width for im, _ in rendered)
            total_h = rendered[0][0].height
            for im, same_prev in rendered[1:]:
                step = (gap_same if same_prev else gap_diff) - overlap
                total_h += im.height + step

            combined = Image.new("RGBA", (max_w, max(1, int(round(total_h)))), (0, 0, 0, 0))
            cy = 0
            for i, (im, same_prev) in enumerate(rendered):
                if i > 0:
                    cy += (gap_same if same_prev else gap_diff) - overlap
                combined.alpha_composite(im, (0, max(0, int(round(cy)))))
                cy += im.height

        # Trim transparent borders while preserving a slight shadow cushion, then fit to <= 512px
        bbox = combined.split()[3].getbbox()
        if bbox:
            combined = combined.crop(bbox)

        # Quotly sticker scaling: longest side is 512px
        w, h = combined.size
        k = 512.0 / max(w, h)
        new_w = max(1, min(512, int(round(w * k))))
        new_h = max(1, min(512, int(round(h * k))))
        return combined.resize((new_w, new_h), Image.LANCZOS)

    except Exception as exc:
        # Never raise on bad input; return a safe valid RGBA image
        print("quote_pil error:", repr(exc))
        err_im = Image.new("RGBA", (512, 128), (0, 0, 0, 0))
        return err_im


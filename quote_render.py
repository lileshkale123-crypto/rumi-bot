"""New /q drawing: steady size, emoji pictures, wide bubble."""
import io

from PIL import Image, ImageDraw, ImageOps

import quote_emoji as qe
import quote_layout as L

W, PAD, AV, PX, PY = 512, 10, 92, 18, 14
BUBBLE, NAME, TEXT = (30, 36, 56, 255), (102, 217, 232), (235, 238, 250)


def _fit(text, bx):
    avail = W - bx - PAD - 2 * PX
    for size in range(36, 21, -1):
        f = L.font(L.REG, size)
        lines = L.layout(L.tokens(text, f, avail), avail)
        lh = size + 14
        if len(lines) * lh <= 512 - 2 * PAD - 2 * PY - 52:
            return f, lines, lh
    room = (512 - 2 * PAD - 2 * PY - 52) // lh
    lines = lines[:room]
    lines[-1] = lines[-1][:-1] + [("w", "…", L.tw("…", f))]
    return f, lines, lh


def render(name, text, avatar, uid):
    name, text = qe.clean(name), qe.clean(text)
    bx = PAD + AV + 10
    f, lines, lh = _fit(text, bx)
    nf = L.font(L.BOLD, 28)
    nrow = L.layout(L.tokens(name, nf, W - bx - PAD - 2 * PX), W - bx - PAD - 2 * PX)[0]
    bh = 2 * PY + 52 + lh * len(lines)
    bw = max([L.row_w(l) for l in lines] + [L.row_w(nrow)]) + 2 * PX
    bw = min(max(bw, 190), W - bx - PAD)
    H = max(AV + 2 * PAD, bh + 2 * PAD)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    ok = False
    if avatar:
        try:
            a = ImageOps.fit(Image.open(io.BytesIO(avatar)).convert("RGBA"), (AV, AV))
            m = Image.new("L", (AV, AV), 0)
            ImageDraw.Draw(m).ellipse([0, 0, AV, AV], fill=255)
            img.paste(a, (PAD, PAD), m)
            ok = True
        except Exception as e:
            print('quote avatar decode failed:', e)
    if not ok:
        dr.ellipse([PAD, PAD, PAD + AV, PAD + AV], fill=BUBBLE)
        dr.text((PAD + AV // 2, PAD + AV // 2), (name[:1] or "?").upper(),
                font=L.font(L.BOLD, 40), anchor="mm", fill=(255, 255, 255))
    dr.rounded_rectangle([bx, PAD, bx + bw, PAD + bh], radius=22, fill=BUBBLE)
    dr.polygon([(bx, PAD + 24), (bx - 12, PAD + 34), (bx, PAD + 44)], fill=BUBBLE)
    L.draw_row(img, dr, bx + PX, PAD + PY - 2, nrow, nf, NAME)
    y = PAD + PY + 46
    for ln in lines:
        L.draw_row(img, dr, bx + PX, y, ln, f, TEXT)
        y += lh
    return img

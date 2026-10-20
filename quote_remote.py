"""/q: ask the QuotLy-style renderer first. None means use our own."""
import base64
import io
import os

import httpx
from PIL import Image

URL = os.environ.get("QUOTE_API_URL", "https://quote-api-production-869b.up.railway.app")


def _msg(m):
    u = m.from_user
    if u:
        frm = {"id": u.id, "first_name": u.first_name, "last_name": u.last_name,
               "username": u.username, "name": u.full_name}
    else:
        c = m.sender_chat
        frm = {"id": c.id if c else 0, "name": (getattr(c, "title", None) or "Someone")}
    frm = {k: v for k, v in frm.items() if v is not None}
    ents = m.entities if m.text else m.caption_entities
    return {"chatId": frm["id"], "avatar": True, "from": frm,
            "text": (m.text or m.caption or ""),
            "entities": [e.to_dict() for e in (ents or [])]}


def _msg_full(m, with_reply=False):
    d = _msg(m)
    r = m.reply_to_message
    if with_reply and r is not None and (r.text or r.caption):
        ru = r.from_user
        d["replyMessage"] = {"name": (ru.full_name if ru else "Someone"),
                             "text": (r.text or r.caption or ""),
                             "chatId": (ru.id if ru else 0), "entities": []}
    return d


def _tag(items, roles):
    if not roles:
        return items
    for d in items:
        val = roles.get(d.get("from", {}).get("id"))
        if val:
            d["senderTag"] = val[1] or val[0]
            d["senderTagRole"] = val[0]
    return items


def _build(msgs, reply=False):
    if reply and len(msgs) == 2:
        a, b = msgs
        d = _msg(b)
        au = a.from_user
        d["replyMessage"] = {"name": (au.full_name if au else "Someone"),
                             "text": (a.text or a.caption or ""),
                             "chatId": (au.id if au else 0), "entities": []}
        return [d]
    return [_msg(m) for m in msgs]


async def remote(token, msgs, roles=None, color=None, reply=False):
    try:
        payload = {"type": "quote", "format": "png", "backgroundColor": color or "#1b1429",
                   "width": 512, "height": 768, "scale": 2, "botToken": token,
                   "emojiBrand": "apple", "messages": _tag(_build(msgs, reply), roles)}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(URL + "/generate", json=payload)
        if r.status_code != 200:
            print("quote remote status", r.status_code)
            return None
        raw = base64.b64decode(r.json()["result"]["image"])
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        box = im.split()[3].getbbox()
        if box:
            im = im.crop(box)
        pad = max(1, round(max(im.size) * 0.09))
        cv = Image.new("RGBA", (im.width, im.height + pad), (0, 0, 0, 0))
        cv.paste(im, (0, 0))
        im = cv
        s = 512 / max(im.size)
        return im.resize((max(1, min(512, round(im.width * s))), max(1, min(512, round(im.height * s)))), Image.LANCZOS)
    except Exception as e:
        print("quote remote failed:", repr(e)[:120])
        return None

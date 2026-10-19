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


def _build(msgs):
    if len(msgs) == 2:
        a, b = msgs
        d = _msg(b)
        au = a.from_user
        d["replyMessage"] = {"name": (au.full_name if au else "Someone"),
                             "text": (a.text or a.caption or ""),
                             "chatId": (au.id if au else 0), "entities": []}
        return [d]
    return [_msg(m) for m in msgs]


async def remote(token, msgs):
    try:
        payload = {"type": "quote", "format": "png", "backgroundColor": "#1b1429",
                   "width": 512, "height": 768, "scale": 2, "botToken": token,
                   "emojiBrand": "apple", "messages": _build(msgs)}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(URL + "/generate", json=payload)
        if r.status_code != 200:
            print("quote remote status", r.status_code)
            return None
        raw = base64.b64decode(r.json()["result"]["image"])
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        s = 512 / max(im.size)
        return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    except Exception as e:
        print("quote remote failed:", repr(e)[:120])
        return None

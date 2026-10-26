"""Hindi swap: English bot text -> lang_hi.json for members who chose Hindi."""
import contextvars
import json
import os
import re

from telegram import Bot, Update
from telegram.ext import TypeHandler

HI_USERS = set()
CUR = contextvars.ContextVar("axi_user", default=None)
EXACT = {}
PATS = []
PH = re.compile(r"%s|%d|\{[a-z_0-9]*\}")


def _load_file():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang_hi.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return
    for k, v in d.items():
        if not v:
            continue
        EXACT[k] = v
        pct = any(t in ("%s", "%d") for t in PH.findall(k))
        kk = k.replace("%%", "%") if pct else k
        hv = v.replace("%%", "%") if pct else v
        toks = PH.findall(kk)
        if not toks or len(PH.sub("", kk).strip()) < 12:
            continue
        rx, pos = "", 0
        for m in PH.finditer(kk):
            rx += re.escape(kk[pos:m.start()]) + "(.*?)"
            pos = m.end()
        rx += re.escape(kk[pos:])
        try:
            PATS.append((re.compile(rx, re.S), hv, toks))
        except re.error:
            pass


def _fill(hv, toks, groups):
    pos = [g for t, g in zip(toks, groups) if t in ("%s", "%d")]
    named = {}
    for t, g in zip(toks, groups):
        if t.startswith("{"):
            named.setdefault(t, g)
    it = iter(pos)

    def sub(m):
        t = m.group(0)
        return next(it) if t in ("%s", "%d") else named[t]

    return PH.sub(sub, hv)


def tr(text):
    if not isinstance(text, str) or not text:
        return text
    v = EXACT.get(text)
    if v:
        return v
    for rx, hv, toks in PATS:
        m = rx.fullmatch(text)
        if m:
            try:
                return _fill(hv, toks, m.groups())
            except Exception:
                return text
    return text


def _hi(chat_id):
    u = chat_id if isinstance(chat_id, int) and chat_id > 0 else CUR.get()
    return u in HI_USERS


def _wrap(name, field, idx):
    orig = getattr(Bot, name)

    async def f(self, *a, **kw):
        try:
            cid = kw.get("chat_id")
            if cid is None and name in ("send_message", "send_photo") and a:
                cid = a[0]
            if HI_USERS and _hi(cid):
                if field in kw:
                    kw[field] = tr(kw[field])
                elif len(a) > idx:
                    a = list(a)
                    a[idx] = tr(a[idx])
                    a = tuple(a)
        except Exception:
            pass
        return await orig(self, *a, **kw)

    setattr(Bot, name, f)


async def _mark(update, context):
    u = update.effective_user
    if u:
        CUR.set(u.id)


async def load(pool):
    try:
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT user_id FROM ax_user_lang WHERE lang='hi'")
        HI_USERS.update(r["user_id"] for r in rows)
    except Exception:
        pass


def set_lang(uid, code):
    if code == "hi":
        HI_USERS.add(uid)
    else:
        HI_USERS.discard(uid)


def register(app):
    _load_file()
    for name, field, idx in (("send_message", "text", 1), ("edit_message_text", "text", 0),
                             ("send_photo", "caption", 2), ("edit_message_caption", "caption", 2)):
        _wrap(name, field, idx)
    app.add_handler(TypeHandler(Update, _mark), group=-101)

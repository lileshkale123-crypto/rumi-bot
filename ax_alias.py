"""Short names for everyday commands: /wal works like /wallet (aiogram 3.x).

The short name is rewritten to the full name before any other middleware or handler sees the
message, so /disable, fun mode, the clean-command settings and every handler treat /wal exactly
like /wallet. Both /wal and /wal@botname work, with or without arguments.
"""
import re
import weakref

from aiogram import BaseMiddleware

# short name -> full name. Only the full names are shown in the "/" menu.
ALIASES = {
    "wal": "wallet",
    "bal": "wallet",
    "daily": "claim",
    "cl": "claim",
    "shield": "guard",
    "gd": "guard",
    "vlt": "vault",
    "dep": "deposit",
    "wd": "withdraw",
    "rank": "rankings",
    "top": "rankings",
    "rich": "toprich",
    "groups": "topgroups",
    "rev": "revive",
    "bty": "bounty",
    "pfp": "profile",
    "pf": "profile",
    "gs": "giftshop",
}

_CMD_RE = re.compile(r"/([A-Za-z0-9_]+)(@[A-Za-z0-9_]+)?(?=\s|$)")


def rewrite(text: str | None):
    """'/wal@bot 5' -> ('/wallet@bot 5', length_change). None when it is not a short command."""
    if not text or text[0] != "/":
        return None
    m = _CMD_RE.match(text)
    if m is None:
        return None
    full = ALIASES.get(m.group(1).lower())
    if full is None:
        return None
    head = "/" + full + (m.group(2) or "")
    return head + text[m.end():], len(head) - m.end()


def _shift(entities, diff: int):
    out = []
    for e in entities or []:
        if e.type == "bot_command" and e.offset == 0:
            out.append(e.model_copy(update={"length": e.length + diff}))
        elif e.offset > 0:
            out.append(e.model_copy(update={"offset": e.offset + diff}))
        else:
            out.append(e)
    return out


class AliasMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            text = getattr(event, "text", None)
            if text:
                r = rewrite(text)
                if r:
                    event = event.model_copy(update={"text": r[0], "entities": _shift(event.entities, r[1])})
            else:
                cap = getattr(event, "caption", None)
                r = rewrite(cap) if cap else None
                if r:
                    event = event.model_copy(update={"caption": r[0], "caption_entities": _shift(event.caption_entities, r[1])})
        except Exception:
            pass  # never block a message because of an alias problem
        return await handler(event, data)


_DISPATCHERS: "weakref.WeakSet" = weakref.WeakSet()


def register(dp) -> None:
    if dp in _DISPATCHERS:
        return
    _DISPATCHERS.add(dp)
    dp.message.outer_middleware(AliasMiddleware())

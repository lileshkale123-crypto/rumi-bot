"""Shared punishment helper: ax_actions.run(context, chat_id, user, action, reason)
Actions: ban mute kick warn tban tmute delete, plus silent s-versions (sban smute skick swarn).
action may carry a time: "tmute 6h" or "tban 1d"."""
import logging
import re
from datetime import datetime, timedelta, timezone

from telegram.constants import ParseMode

import ax_mod
import ax_warn
from ax_common import esc

log = logging.getLogger(__name__)
_DUR = re.compile(r"^(\d+)([smhdw])$", re.I)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
KNOWN = {"ban", "mute", "kick", "warn", "tban", "tmute", "delete", "nothing"}


def parse_dur(t):
    m = _DUR.match((t or "").strip())
    return int(m[1]) * _UNITS[m[2].lower()] if m else None


def parse_action(text):
    """'tmute 6h' -> ('tmute', 21600, False). 'sban' -> ('ban', None, True). Bad -> (None, None, False)."""
    parts = (text or "").lower().split()
    if not parts:
        return None, None, False
    name = parts[0]
    silent = False
    if name in ("sban", "smute", "skick", "swarn"):
        silent, name = True, name[1:]
    if name not in KNOWN:
        return None, None, False
    secs = parse_dur(parts[1]) if len(parts) > 1 else None
    if name in ("tban", "tmute"):
        secs = secs or 3600
    return name, secs, silent


async def run(context, chat_id, user, action, reason=None):
    name, secs, silent = parse_action(action)
    if name is None or name in ("nothing", "delete"):
        return
    pool = context.bot_data["pool"]
    bot = context.bot
    icon, title = "⚠️", "Warned"
    if name == "warn":
        async with pool.acquire() as c:
            s = await ax_warn.get_settings(c, chat_id)
            await c.execute("INSERT INTO ax_warn_log (chat_id, user_id, reason) VALUES ($1,$2,$3)",
                            chat_id, user.id, reason)
            n = await ax_warn.count_active(c, chat_id, user.id, s["warn_expire_secs"])
        if n >= s["warn_limit"]:
            await ax_warn.punish(context, pool, chat_id, user.id, s["warn_mode"], s["warn_mode_secs"])
            async with pool.acquire() as c:
                await c.execute("DELETE FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2", chat_id, user.id)
            icon, title = ax_warn.ICONS.get(s["warn_mode"], "⚠️"), "Warn limit reached"
            extra = f"\n📊 <b>{n}/{s['warn_limit']}</b> → <b>{s['warn_mode']}</b>"
        else:
            extra = f"\n📊 Warnings: <b>{n}/{s['warn_limit']}</b>"
    elif name in ("tban", "tmute"):
        await ax_warn.punish(context, pool, chat_id, user.id, name, secs)
        icon, title = ax_warn.ICONS[name], "Banned" if name == "tban" else "Muted"
        extra = f"\n⏳ For <b>{secs // 3600 if secs >= 3600 else secs // 60}{'h' if secs >= 3600 else 'm'}</b>"
    else:
        await ax_mod.perform(name, bot, pool, chat_id, user.id)
        icon, title = ax_mod.META[name]
        extra = ""
    if reason:
        extra += f"\n📝 {esc(reason)}"
    try:
        import ax_log
        cat = {'warn': 'warn', 'tban': 'ban', 'ban': 'ban',
               'tmute': 'mute', 'mute': 'mute',
               'kick': 'kick'}.get(name, 'settings')
        _r = str(reason or "")
        if _r.startswith("Flooding"):
            cat = "flood"
        elif _r.startswith("Locked"):
            cat = "lock"
        elif _r.startswith("Blocklisted"):
            cat = "blocklist"
        await ax_log.send_log(
            bot, pool, chat_id, cat,
            f'<b>{name}</b>: {esc(user.first_name)} '
            f'(<code>{user.id}</code>){extra}')
    except Exception:
        pass
    try:
        import ax_misc3
        if chat_id in ax_misc3.SILENT:
            return
    except Exception:
        pass
    if silent:
        return
    try:
        await bot.send_message(chat_id, ax_mod.card(icon, title, user.first_name, extra), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("action notice failed: %s", e)

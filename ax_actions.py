"""Shared punishment helper: ax_actions.run(bot, pool, chat_id, user, action, reason)
Actions: ban mute kick warn tban tmute delete, plus silent s-versions (sban smute skick swarn).
action may carry a time: "tmute 6h" or "tban 1d"."""
import logging
import re

import asyncpg
from aiogram import Bot
from aiogram.enums import ParseMode

import ax_mod
import ax_warn
import ax_warn2
from ax_common import esc, fire

log = logging.getLogger(__name__)
_DUR = re.compile(r"^(\d+)([smhdw])$", re.I)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
KNOWN = {"ban", "mute", "kick", "warn", "tban", "tmute", "delete", "nothing"}


def parse_dur(t: str | None) -> int | None:
    m = _DUR.match((t or "").strip())
    return int(m[1]) * _UNITS[m[2].lower()] if m else None


def parse_action(text: str | None) -> tuple[str | None, int | None, bool]:
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


async def run(
    bot: Bot,
    pool: asyncpg.Pool,
    chat_id: int,
    user,
    action: str,
    reason: str | None = None,
) -> None:
    name, secs, silent = parse_action(action)
    if name is None or name in ("nothing", "delete"):
        return

    icon, title = "⚠️", "Warned"
    first_name = getattr(user, "first_name", None) or "User"

    if name == "warn":
        s, n = await ax_warn.add_warn(pool, chat_id, user.id, reason)
        if n >= s["warn_limit"]:
            await ax_warn.punish(bot, pool, chat_id, user.id, s["warn_mode"], s["warn_mode_secs"])
            async with pool.acquire() as c:
                await c.execute(
                    "DELETE FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2",
                    chat_id,
                    user.id,
                )
            icon, title = ax_warn.ICONS.get(s["warn_mode"], "⚠️"), "Warn limit reached"
            extra = f"\n📊 <b>{n}/{s['warn_limit']}</b> → <b>{s['warn_mode']}</b>"
        else:
            extra = f"\n📊 Warnings: <b>{n}/{s['warn_limit']}</b>"
    elif name in ("tban", "tmute"):
        await ax_warn.punish(bot, pool, chat_id, user.id, name, secs or 3600)
        icon, title = ax_warn.ICONS[name], "Banned" if name == "tban" else "Muted"
        extra = f"\n⏳ For <b>{ax_warn2.fmt_time(secs or 3600)}</b>"
    else:
        await ax_mod.perform(name, bot, pool, chat_id, user.id)
        icon, title = ax_mod.META[name]
        extra = ""

    if reason:
        extra += f"\n📝 {esc(reason)}"

    try:
        import ax_log

        cat = {
            "warn": "warn",
            "tban": "ban",
            "ban": "ban",
            "tmute": "mute",
            "mute": "mute",
            "kick": "kick",
        }.get(name, "settings")
        r_str = str(reason or "")
        if r_str.startswith("Flooding"):
            cat = "flood"
        elif r_str.startswith("Locked"):
            cat = "lock"
        elif r_str.startswith("Blocklisted"):
            cat = "blocklist"
        fire(
            ax_log.send_log(
                bot,
                pool,
                chat_id,
                cat,
                f"<b>{name}</b>: {esc(first_name)} (<code>{user.id}</code>){extra}",
            )
        )
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

    tid = None
    try:
        import ax_actiontopic

        tid = ax_actiontopic.MAP.get(chat_id)
    except Exception:
        tid = None

    card_text = ax_mod.card(icon, title, first_name, extra)
    if tid:
        try:
            await bot.send_message(
                chat_id,
                card_text,
                parse_mode=ParseMode.HTML,
                message_thread_id=tid,
            )
            return
        except Exception:
            pass
    try:
        await bot.send_message(chat_id, card_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("action notice failed: %s", e)

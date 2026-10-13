"""Warn settings: /warnlimit /warnmode /warntime /resetwarn /resetallwarns /warnings."""
import logging

from telegram.ext import CommandHandler

import ax_mod
import ax_warn
from ax_common import esc, is_admin

log = logging.getLogger(__name__)
MODES = ("ban", "mute", "kick", "tban", "tmute")
DUR_WORDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_time(t):
    t = (t or "").lower().strip()
    if len(t) < 2 or t[-1] not in DUR_WORDS or not t[:-1].isdigit():
        return None
    return int(t[:-1]) * DUR_WORDS[t[-1]]


def fmt_time(secs):
    if secs <= 0:
        return "off"
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs % size == 0:
            return f"{secs // size}{unit}"
    return f"{secs}s"


async def admin_only(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


async def save(pool, chat_id, col, value):
    # col is always one of our own fixed column names, never user input
    assert col in ("warn_limit", "warn_mode", "warn_mode_secs", "warn_expire_secs")
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_warn_settings (chat_id, {col}) VALUES ($1,$2) "
            f"ON CONFLICT (chat_id) DO UPDATE SET {col}=$2", chat_id, value)


async def warnlimit_cmd(update, context):
    if not await admin_only(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    args = context.args or []
    if not args:
        async with pool.acquire() as c:
            s = await ax_warn.get_settings(c, chat_id)
        await ax_mod.say(update, f"📊 <b>Warn limit</b>\n<blockquote>Currently <b>{s['warn_limit']}</b> warnings</blockquote>")
        return
    if not args[0].isdigit() or not 1 <= int(args[0]) <= 100:
        await ax_mod.say(update, "💫 <b>Pick a number</b>\n<blockquote>Example: /warnlimit 5 (1 to 100)</blockquote>")
        return
    await save(pool, chat_id, "warn_limit", int(args[0]))
    await ax_mod.say(update, f"✅ <b>Warn limit set</b>\n<blockquote>Punishment after <b>{int(args[0])}</b> warnings</blockquote>")


async def warnmode_cmd(update, context):
    if not await admin_only(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    args = context.args or []
    if not args:
        async with pool.acquire() as c:
            s = await ax_warn.get_settings(c, chat_id)
        extra = f" for {fmt_time(s['warn_mode_secs'])}" if s["warn_mode"] in ("tban", "tmute") else ""
        await ax_mod.say(update, f"⚙️ <b>Warn mode</b>\n<blockquote>Currently <b>{s['warn_mode']}</b>{extra}</blockquote>")
        return
    mode = args[0].lower()
    if mode not in MODES:
        await ax_mod.say(update, "💫 <b>Pick a mode</b>\n<blockquote>ban / mute / kick / tban / tmute\nExample: /warnmode tmute 6h</blockquote>")
        return
    secs = 86400
    if mode in ("tban", "tmute"):
        if len(args) > 1:
            secs = parse_time(args[1])
            if not secs:
                await ax_mod.say(update, "⏳ <b>Bad time</b>\n<blockquote>Example: /warnmode tmute 6h (s, m, h, d, w)</blockquote>")
                return
        await save(pool, chat_id, "warn_mode_secs", secs)
    await save(pool, chat_id, "warn_mode", mode)
    extra = f" for <b>{fmt_time(secs)}</b>" if mode in ("tban", "tmute") else ""
    await ax_mod.say(update, f"✅ <b>Warn mode set</b>\n<blockquote>Limit reached → <b>{mode}</b>{extra}</blockquote>")


async def warntime_cmd(update, context):
    if not await admin_only(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    args = context.args or []
    if not args:
        async with pool.acquire() as c:
            s = await ax_warn.get_settings(c, chat_id)
        await ax_mod.say(update, f"⏳ <b>Warn expiry</b>\n<blockquote>Warnings last: <b>{fmt_time(s['warn_expire_secs'])}</b></blockquote>")
        return
    if args[0].lower() in ("off", "no", "0"):
        await save(pool, chat_id, "warn_expire_secs", 0)
        await ax_mod.say(update, "✅ <b>Expiry off</b>\n<blockquote>Warnings now last forever</blockquote>")
        return
    secs = parse_time(args[0])
    if not secs:
        await ax_mod.say(update, "⏳ <b>Bad time</b>\n<blockquote>Example: /warntime 4w (s, m, h, d, w) or /warntime off</blockquote>")
        return
    await save(pool, chat_id, "warn_expire_secs", secs)
    await ax_mod.say(update, f"✅ <b>Expiry set</b>\n<blockquote>Warnings vanish after <b>{fmt_time(secs)}</b></blockquote>")


async def resetwarn_cmd(update, context):
    target = await ax_mod.gate(update, context, False)
    if target is None:
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2", chat_id, target.id)
    await ax_mod.say(update, ax_mod.card("✅", "Warnings reset", target.first_name, "\n📊 Back to 0"))


async def resetallwarns_cmd(update, context):
    if not await admin_only(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_warn_log WHERE chat_id=$1", chat_id)
    await ax_mod.say(update, "✅ <b>All warnings cleared</b>\n<blockquote>Everyone is back to 0 💜</blockquote>")


async def warnings_cmd(update, context):
    if not await admin_only(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    async with pool.acquire() as c:
        s = await ax_warn.get_settings(c, chat_id)
    extra = f" ({fmt_time(s['warn_mode_secs'])})" if s["warn_mode"] in ("tban", "tmute") else ""
    await ax_mod.say(update, (
        "⚙️ <b>Warn settings</b>\n<blockquote>"
        f"📊 <b>Limit:</b> {s['warn_limit']}\n"
        f"🔨 <b>Action:</b> {s['warn_mode']}{extra}\n"
        f"⏳ <b>Expiry:</b> {fmt_time(s['warn_expire_secs'])}"
        "</blockquote>"))


def register(app):
    app.add_handler(CommandHandler("warnlimit", warnlimit_cmd))
    app.add_handler(CommandHandler("warnmode", warnmode_cmd))
    app.add_handler(CommandHandler("warntime", warntime_cmd))
    app.add_handler(CommandHandler("resetwarn", resetwarn_cmd))
    app.add_handler(CommandHandler("resetallwarns", resetallwarns_cmd))
    app.add_handler(CommandHandler("warnings", warnings_cmd))

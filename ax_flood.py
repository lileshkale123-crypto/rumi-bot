"""Flood extras: /flood /setfloodtimer /floodmode /clearflood + punishment used by the flood watcher."""
import time

from telegram.ext import CommandHandler, MessageHandler, filters

import ax_actions
import ax_mod
import ax_settings
from ax_common import is_admin
from ax_warn2 import fmt_time, parse_time

MODES = ("ban", "mute", "kick", "tban", "tmute")
_recent = {}  # (chat_id, user_id) -> [(time, message_id)]


async def record(update, context):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not user or user.is_bot:
        return
    k = (chat.id, user.id)
    now = time.time()
    lst = [x for x in _recent.get(k, []) if now - x[0] < 120][-49:]
    lst.append((now, msg.message_id))
    _recent[k] = lst


async def get(pool, chat_id):
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT flood_limit, flood_window, flood_mute, flood_mode, flood_mode_secs, flood_clear "
            "FROM ax_settings WHERE chat_id=$1", chat_id)
    if r:
        return dict(r)
    return {"flood_limit": 0, "flood_window": 5, "flood_mute": 300,
            "flood_mode": "tmute", "flood_mode_secs": 0, "flood_clear": False}


async def punish(context, chat, user, msg, mute_secs):
    s = await get(context.bot_data["pool"], chat.id)
    if s["flood_clear"]:
        ids = [m for _, m in _recent.pop((chat.id, user.id), [])]
        if msg.message_id not in ids:
            ids.append(msg.message_id)
        try:
            await context.bot.delete_messages(chat.id, ids)
        except Exception:
            pass
    mode, secs = s["flood_mode"], s["flood_mode_secs"]
    if mode in ("tban", "tmute"):
        action = f"{mode} {secs or (mute_secs if mode == 'tmute' else 3600)}s"
    else:
        action = mode
    await ax_actions.run(context, chat.id, user, action, "Flooding the chat")


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


async def flood_cmd(update, context):
    if not await _gate(update, context):
        return
    s = await get(context.bot_data["pool"], update.effective_chat.id)
    if s["flood_limit"] == 0:
        await ax_mod.say(update, "🌊 <b>Antiflood is off</b>\n<blockquote>Turn on: /setflood 6 (messages) or /setfloodtimer 10 30s</blockquote>")
        return
    mode = s["flood_mode"]
    if mode in ("tban", "tmute"):
        mode += f" {fmt_time(s['flood_mode_secs'] or s['flood_mute'])}"
    await ax_mod.say(update, (
        "🌊 <b>Antiflood</b>\n<blockquote>"
        f"📊 <b>Trigger:</b> {s['flood_limit']} messages in {s['flood_window']}s\n"
        f"🔨 <b>Action:</b> {mode}\n"
        f"🧹 <b>Delete flood:</b> {'on' if s['flood_clear'] else 'off'}</blockquote>"))


async def setfloodtimer_cmd(update, context):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    args = context.args or []
    if args and args[0].lower() in ("off", "no"):
        await ax_settings.set_limit(pool, chat_id, 0)
        await ax_mod.say(update, "🌊 <b>Antiflood off</b>\n<blockquote>No flood checks now</blockquote>")
        return
    secs = parse_time(args[1]) if len(args) > 1 else None
    if len(args) < 2 or not args[0].isdigit() or int(args[0]) < 3 or not secs or secs > 600:
        await ax_mod.say(update, "💫 <b>Usage</b>\n<blockquote>/setfloodtimer 10 30s: 10 messages within 30 seconds\n/setfloodtimer off</blockquote>")
        return
    async with pool.acquire() as c:
        await c.execute("INSERT INTO ax_settings (chat_id, flood_limit, flood_window) VALUES ($1,$2,$3) "
                        "ON CONFLICT (chat_id) DO UPDATE SET flood_limit=$2, flood_window=$3",
                        chat_id, int(args[0]), secs)
    ax_settings._cache.pop(chat_id, None)
    await ax_mod.say(update, f"✅ <b>Timed antiflood set</b>\n<blockquote>{args[0]} messages within {fmt_time(secs)} triggers it 🌊</blockquote>")


async def floodmode_cmd(update, context):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    if not args or args[0] not in MODES:
        s = await get(pool, chat_id)
        await ax_mod.say(update, f"⚙️ <b>Flood action</b>\n<blockquote>Currently <b>{s['flood_mode']}</b>\nSet: /floodmode ban, mute, kick, tban or tmute (example: /floodmode tmute 3h)</blockquote>")
        return
    secs = 0
    if args[0] in ("tban", "tmute") and len(args) > 1:
        secs = parse_time(args[1])
        if not secs:
            await ax_mod.say(update, "⏳ <b>Bad time</b>\n<blockquote>Example: /floodmode tmute 3h</blockquote>")
            return
    async with pool.acquire() as c:
        await c.execute("INSERT INTO ax_settings (chat_id, flood_mode, flood_mode_secs) VALUES ($1,$2,$3) "
                        "ON CONFLICT (chat_id) DO UPDATE SET flood_mode=$2, flood_mode_secs=$3", chat_id, args[0], secs)
    ax_settings._cache.pop(chat_id, None)
    await ax_mod.say(update, f"✅ <b>Flood action set</b>\n<blockquote>Flooders get <b>{args[0]}</b></blockquote>")


async def clearflood_cmd(update, context):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    arg = (context.args or [""])[0].lower()
    if arg not in ("on", "off", "yes", "no"):
        s = await get(pool, chat_id)
        await ax_mod.say(update, f"⚙️ <b>Delete flood</b>\n<blockquote>Currently <b>{'on' if s['flood_clear'] else 'off'}</b>\nUse /clearflood on or off</blockquote>")
        return
    val = arg in ("on", "yes")
    async with pool.acquire() as c:
        await c.execute("INSERT INTO ax_settings (chat_id, flood_clear) VALUES ($1,$2) "
                        "ON CONFLICT (chat_id) DO UPDATE SET flood_clear=$2", chat_id, val)
    ax_settings._cache.pop(chat_id, None)
    await ax_mod.say(update, f"✅ <b>Delete flood {'on' if val else 'off'}</b>\n<blockquote>Flood messages are {'deleted' if val else 'kept'}</blockquote>")


def register(app):
    app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.PRIVATE, record), group=-7)
    app.add_handler(CommandHandler("flood", flood_cmd))
    app.add_handler(CommandHandler("setfloodtimer", setfloodtimer_cmd))
    app.add_handler(CommandHandler("floodmode", floodmode_cmd))
    app.add_handler(CommandHandler("clearflood", clearflood_cmd))


"""Antiraid: /antiraid /raidtime /raidactiontime /autoantiraid + join watcher."""
import logging
import time
from datetime import datetime, timedelta, timezone

from telegram.ext import ApplicationHandlerStop, CommandHandler, MessageHandler, filters

import ax_mod
from ax_common import is_admin
from ax_warn2 import fmt_time, parse_time

log = logging.getLogger(__name__)
_joins = {}  # chat_id -> list of join timestamps


async def get(pool, chat_id):
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM ax_raid_settings WHERE chat_id=$1", chat_id)
    if r:
        return dict(r)
    return {"raid_until": None, "raid_secs": 21600, "action_secs": 3600, "auto_limit": 0}


async def save(pool, chat_id, col, val):
    assert col in ("raid_until", "raid_secs", "action_secs", "auto_limit")
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_raid_settings (chat_id, {col}) VALUES ($1,$2) "
            f"ON CONFLICT (chat_id) DO UPDATE SET {col}=$2", chat_id, val)


def active(s):
    return bool(s["raid_until"] and s["raid_until"] > datetime.now(timezone.utc))


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


async def antiraid_cmd(update, context):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    s = await get(pool, chat_id)
    args = context.args or []
    if args and args[0].lower() in ("off", "no"):
        await save(pool, chat_id, "raid_until", None)
        await ax_mod.say(update, "✅ <b>Antiraid off</b>\n<blockquote>New members can join normally again</blockquote>")
        return
    if not args and active(s):
        left = int((s["raid_until"] - datetime.now(timezone.utc)).total_seconds())
        await ax_mod.say(update, f"🚨 <b>Antiraid is ON</b>\n<blockquote>New joins are temp-banned for {fmt_time(s['action_secs'])}\n⏳ Ends in <b>{fmt_time(max(60, left // 60 * 60))}</b>\nTurn off: /antiraid off</blockquote>")
        return
    secs = parse_time(args[0]) if args else s["raid_secs"]
    if not secs:
        await ax_mod.say(update, "⏳ <b>Bad time</b>\n<blockquote>Example: /antiraid 3h (s, m, h, d, w) or /antiraid off</blockquote>")
        return
    await save(pool, chat_id, "raid_until", datetime.now(timezone.utc) + timedelta(seconds=secs))
    await ax_mod.say(update, f"🚨 <b>Antiraid ON</b>\n<blockquote>For <b>{fmt_time(secs)}</b>, every new member is banned for <b>{fmt_time(s['action_secs'])}</b>\nTurn off: /antiraid off</blockquote>")


async def _time_cmd(update, context, col, label, example):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    args = context.args or []
    if not args:
        s = await get(pool, chat_id)
        await ax_mod.say(update, f"⚙️ <b>{label}</b>\n<blockquote>Currently <b>{fmt_time(s[col])}</b></blockquote>")
        return
    secs = parse_time(args[0])
    if not secs:
        await ax_mod.say(update, f"⏳ <b>Bad time</b>\n<blockquote>Example: {example}</blockquote>")
        return
    await save(pool, chat_id, col, secs)
    await ax_mod.say(update, f"✅ <b>{label} set</b>\n<blockquote><b>{fmt_time(secs)}</b></blockquote>")


async def raidtime_cmd(update, context):
    await _time_cmd(update, context, "raid_secs", "Raid duration", "/raidtime 6h")


async def raidactiontime_cmd(update, context):
    await _time_cmd(update, context, "action_secs", "Raid ban length", "/raidactiontime 1h")


async def autoantiraid_cmd(update, context):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    args = context.args or []
    if not args:
        s = await get(pool, chat_id)
        cur = f"{s['auto_limit']} joins per minute" if s["auto_limit"] else "off"
        await ax_mod.say(update, f"⚙️ <b>Auto antiraid</b>\n<blockquote>Currently <b>{cur}</b>\nExample: /autoantiraid 15 or /autoantiraid off</blockquote>")
        return
    if args[0].lower() in ("off", "no", "0"):
        await save(pool, chat_id, "auto_limit", 0)
        await ax_mod.say(update, "✅ <b>Auto antiraid off</b>\n<blockquote>I won't switch it on by myself</blockquote>")
        return
    if not args[0].isdigit() or not 2 <= int(args[0]) <= 500:
        await ax_mod.say(update, "💫 <b>Pick a number</b>\n<blockquote>Example: /autoantiraid 15 (2 to 500 joins per minute)</blockquote>")
        return
    await save(pool, chat_id, "auto_limit", int(args[0]))
    await ax_mod.say(update, f"✅ <b>Auto antiraid on</b>\n<blockquote>If <b>{args[0]}</b> people join within a minute, antiraid starts by itself 🚨</blockquote>")


async def join_watcher(update, context):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not msg.new_chat_members:
        return
    pool = context.bot_data["pool"]
    s = await get(pool, chat.id)
    now = time.time()
    if s["auto_limit"] > 0:
        lst = [t for t in _joins.get(chat.id, []) if now - t < 60] + [now] * len(msg.new_chat_members)
        _joins[chat.id] = lst
        if len(lst) >= s["auto_limit"] and not active(s):
            until = datetime.now(timezone.utc) + timedelta(seconds=s["raid_secs"])
            await save(pool, chat.id, "raid_until", until)
            s["raid_until"] = until
            try:
                await context.bot.send_message(chat.id, f"🚨 <b>Raid detected</b>\n<blockquote>{len(lst)} joins in a minute. Antiraid is ON for {fmt_time(s['raid_secs'])}.\nTurn off: /antiraid off</blockquote>", parse_mode="HTML")
            except Exception:
                pass
    if not active(s):
        return
    until = datetime.now(timezone.utc) + timedelta(seconds=s["action_secs"])
    for m in msg.new_chat_members:
        if m.id == context.bot.id:
            continue
        try:
            await context.bot.ban_chat_member(chat.id, m.id, until_date=until)
        except Exception as e:
            log.warning("raid ban failed: %s", e)
    try:
        await msg.delete()
    except Exception:
        pass
    raise ApplicationHandlerStop


def register(app):
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, join_watcher), group=-3)
    app.add_handler(CommandHandler("antiraid", antiraid_cmd))
    app.add_handler(CommandHandler("raidtime", raidtime_cmd))
    app.add_handler(CommandHandler("raidactiontime", raidactiontime_cmd))
    app.add_handler(CommandHandler("autoantiraid", autoantiraid_cmd))

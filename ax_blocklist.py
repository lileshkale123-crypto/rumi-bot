"""Blocklists: /addblocklist /rmblocklist /blocklist /unblocklistall /blocklistmode
/blocklistdelete /setblocklistreason /resetblocklistreason + watcher (group -4)."""
import logging
import re
import time

from telegram.constants import ChatMemberStatus
from telegram.ext import ApplicationHandlerStop, CommandHandler, MessageHandler, filters

import ax_actions
import ax_approve
import ax_lockcore
import ax_mod
from ax_common import esc, is_admin

log = logging.getLogger(__name__)
MODES = ("nothing", "ban", "mute", "kick", "warn", "tban", "tmute")
_TAG = re.compile(r"\{([^}]+)\}")
_cache = {}  # chat_id -> (time, rows, settings)


def parse_triggers(body):
    """'(hi, "hi there") reason' or '"a b" reason' or 'word reason' -> (triggers, rest)."""
    body = body.strip()
    if body.startswith("("):
        end = body.find(")")
        if end == -1:
            return [], body
        inner, rest = body[1:end], body[end + 1:]
        trig = [t.strip().strip('"') for t in re.findall(r'"[^"]*"|[^,]+', inner)]
        return [t for t in trig if t], rest.strip()
    if body.startswith('"'):
        end = body.find('"', 1)
        if end == -1:
            return [], body
        return [body[1:end]], body[end + 1:].strip()
    parts = body.split(None, 1)
    return ([parts[0]] if parts else []), (parts[1] if len(parts) > 1 else "")


def compile_trigger(trig):
    t = trig.strip()
    low = t.lower()
    if low.startswith("exact:"):
        return re.compile(r"^\s*" + _glob(t[6:]) + r"\s*$", re.I)
    if low.startswith("prefix:"):
        return re.compile(r"^\s*" + _glob(t[7:]), re.I)
    return re.compile(r"(?<!\w)" + _glob(t) + r"(?!\w)", re.I)


def _glob(s):
    out, i = [], 0
    while i < len(s):
        if s.startswith("**", i):
            out.append(".*"); i += 2
        elif s[i] == "*":
            out.append(r"\S*"); i += 1
        elif s[i] == "?":
            out.append(r"\S"); i += 1
        else:
            out.append(re.escape(s[i])); i += 1
    return "".join(out)


async def load(pool, chat_id):
    hit = _cache.get(chat_id)
    if hit and time.time() - hit[0] < 30:
        return hit[1], hit[2]
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT trigger, reason FROM ax_blocklist WHERE chat_id=$1", chat_id)
        s = await c.fetchrow("SELECT * FROM ax_blocklist_settings WHERE chat_id=$1", chat_id)
    s = dict(s) if s else {"mode": "nothing", "mode_secs": 3600, "delete_msg": True, "default_reason": None}
    comp = []
    for r in rows:
        try:
            comp.append((compile_trigger(r["trigger"]), r["trigger"], r["reason"]))
        except re.error:
            pass
    _cache[chat_id] = (time.time(), comp, s)
    return comp, s


def invalidate(chat_id):
    _cache.pop(chat_id, None)


async def watcher(update, context):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not user or user.is_bot:
        return
    text = msg.text or msg.caption
    if not text:
        return
    pool = context.bot_data["pool"]
    comp, s = await load(pool, chat.id)
    if not comp:
        return
    hit = next(((p, t, r) for p, t, r in comp if p.search(text)), None)
    if hit is None:
        return
    if await ax_approve.is_approved(pool, chat.id, user.id) or await ax_lockcore._is_admin_cached(context.bot, chat.id, user.id):
        return
    _, trig, raw_reason = hit
    tags = [m.strip() for m in _TAG.findall(raw_reason or "")]
    reason = _TAG.sub("", raw_reason or "").strip() or s["default_reason"]
    delete = s["delete_msg"]
    action = s["mode"] if s["mode"] == "nothing" else (s["mode"] + (f" {s['mode_secs']}s" if s["mode"] in ("tban", "tmute") else ""))
    for t in tags:
        low = t.lower()
        if low == "nodel":
            delete = False
        elif low == "del":
            delete = True
        elif ax_actions.parse_action(t)[0] is not None:
            action = t
    if delete:
        try:
            await msg.delete()
        except Exception:
            pass
    if action and action != "nothing":
        try:
            await ax_actions.run(context, chat.id, user, action, reason or f"Blocklisted: {trig}")
        except Exception as e:
            log.warning("blocklist action failed: %s", e)
    if delete:
        raise ApplicationHandlerStop


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


async def add_cmd(update, context):
    if not await _gate(update, context):
        return
    text = update.effective_message.text or ""
    body = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    trigs, reason = parse_triggers(body)
    if not trigs:
        await ax_mod.say(update, '🚫 <b>Add what?</b>\n<blockquote>/addblocklist word reason\n/addblocklist "a sentence" reason\n/addblocklist (hi, hey) reason\nWildcards: ? * **\nActions: {tmute 6h} {warn} {nodel}</blockquote>')
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        for t in trigs:
            await c.execute("INSERT INTO ax_blocklist (chat_id, trigger, reason) VALUES ($1,$2,$3) "
                            "ON CONFLICT (chat_id, trigger) DO UPDATE SET reason=$3", chat_id, t.lower(), reason or None)
    invalidate(chat_id)
    await ax_mod.say(update, f"🚫 <b>Blocklisted</b>\n<blockquote>{esc(', '.join(trigs))}</blockquote>")


async def rm_cmd(update, context):
    if not await _gate(update, context):
        return
    text = update.effective_message.text or ""
    body = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    trigs, _ = parse_triggers(body)
    if not trigs:
        await ax_mod.say(update, "💫 <b>Remove what?</b>\n<blockquote>Example: /rmblocklist word</blockquote>")
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_blocklist WHERE chat_id=$1 AND trigger = ANY($2::text[])",
                        chat_id, [t.lower() for t in trigs])
    invalidate(chat_id)
    await ax_mod.say(update, f"✅ <b>Removed</b>\n<blockquote>{esc(', '.join(trigs))}</blockquote>")


async def list_cmd(update, context):
    if not await _gate(update, context):
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        rows = await c.fetch("SELECT trigger FROM ax_blocklist WHERE chat_id=$1 ORDER BY trigger LIMIT 100", chat_id)
    if not rows:
        await ax_mod.say(update, "🚫 <b>Blocklist</b>\n<blockquote>Empty</blockquote>")
        return
    await ax_mod.say(update, "🚫 <b>Blocklist</b>\n<blockquote>" + "\n".join(f"• {esc(r['trigger'])}" for r in rows) + "</blockquote>")


async def clearall_cmd(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        return
    m = await context.bot.get_chat_member(chat.id, user.id)
    if m.status != ChatMemberStatus.OWNER:
        await ax_mod.say(update, "👑 <b>Owner only</b>\n<blockquote>Only the group creator can clear the whole blocklist</blockquote>")
        return
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_blocklist WHERE chat_id=$1", chat.id)
    invalidate(chat.id)
    await ax_mod.say(update, "✅ <b>Blocklist cleared</b>\n<blockquote>All triggers removed</blockquote>")


async def _set(context, chat_id, col, val):
    assert col in ("mode", "mode_secs", "delete_msg", "default_reason")
    async with context.bot_data["pool"].acquire() as c:
        await c.execute(f"INSERT INTO ax_blocklist_settings (chat_id, {col}) VALUES ($1,$2) "
                        f"ON CONFLICT (chat_id) DO UPDATE SET {col}=$2", chat_id, val)
    invalidate(chat_id)


async def mode_cmd(update, context):
    if not await _gate(update, context):
        return
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    if not args or args[0] not in MODES:
        _, s = await load(context.bot_data["pool"], chat_id)
        await ax_mod.say(update, f"⚙️ <b>Blocklist mode</b>\n<blockquote>Currently <b>{s['mode']}</b>\nSet: /blocklistmode nothing/ban/mute/kick/warn/tban/tmute</blockquote>")
        return
    if args[0] in ("tban", "tmute") and len(args) > 1:
        secs = ax_actions.parse_dur(args[1])
        if not secs:
            await ax_mod.say(update, "⏳ <b>Bad time</b>\n<blockquote>Example: /blocklistmode tmute 6h</blockquote>")
            return
        await _set(context, chat_id, "mode_secs", secs)
    await _set(context, chat_id, "mode", args[0])
    await ax_mod.say(update, f"✅ <b>Mode set</b>\n<blockquote>Blocklisted words → <b>{args[0]}</b></blockquote>")


async def delete_cmd(update, context):
    if not await _gate(update, context):
        return
    chat_id = update.effective_chat.id
    arg = (context.args or [""])[0].lower()
    if arg not in ("on", "off", "yes", "no"):
        _, s = await load(context.bot_data["pool"], chat_id)
        await ax_mod.say(update, f"⚙️ <b>Blocklist delete</b>\n<blockquote>Currently <b>{'on' if s['delete_msg'] else 'off'}</b>\nUse /blocklistdelete on or off</blockquote>")
        return
    val = arg in ("on", "yes")
    await _set(context, chat_id, "delete_msg", val)
    await ax_mod.say(update, f"✅ <b>Delete {'on' if val else 'off'}</b>\n<blockquote>Blocklisted messages are {'deleted' if val else 'kept'}</blockquote>")


async def setreason_cmd(update, context):
    if not await _gate(update, context):
        return
    text = update.effective_message.text or ""
    body = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
    if not body:
        await ax_mod.say(update, "💫 <b>Reason?</b>\n<blockquote>Example: /setblocklistreason Please follow the rules</blockquote>")
        return
    await _set(context, update.effective_chat.id, "default_reason", body)
    await ax_mod.say(update, "✅ <b>Default reason set</b>\n<blockquote>" + esc(body) + "</blockquote>")


async def resetreason_cmd(update, context):
    if not await _gate(update, context):
        return
    await _set(context, update.effective_chat.id, "default_reason", None)
    await ax_mod.say(update, "✅ <b>Default reason cleared</b>\n<blockquote>Back to no reason</blockquote>")


def register(app):
    app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.PRIVATE & ~filters.COMMAND, watcher), group=-4)
    app.add_handler(CommandHandler("addblocklist", add_cmd))
    app.add_handler(CommandHandler("rmblocklist", rm_cmd))
    app.add_handler(CommandHandler("blocklist", list_cmd))
    app.add_handler(CommandHandler("unblocklistall", clearall_cmd))
    app.add_handler(CommandHandler("blocklistmode", mode_cmd))
    app.add_handler(CommandHandler("blocklistdelete", delete_cmd))
    app.add_handler(CommandHandler("setblocklistreason", setreason_cmd))
    app.add_handler(CommandHandler("resetblocklistreason", resetreason_cmd))

"""Lock commands: /lock /unlock /locks /locktypes /lockwarns /allowlist /rmallowlist /rmallowlistall"""
import re

from telegram.ext import CommandHandler

import ax_actions
import ax_lockcore as core
import ax_mod
from ax_common import esc, is_admin

_ACT = re.compile(r"\{([^}]+)\}")


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


def _split(update):
    """Return (types list, reason or None, action or None, has_hash)."""
    text = update.effective_message.text or ""
    body = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    head, sep, tail = body.partition("###")
    types = [t.lower() for t in head.split()]
    action, reason = None, None
    if sep:
        m = _ACT.search(tail)
        if m:
            action = m.group(1).strip()
            tail = _ACT.sub("", tail)
        reason = tail.strip() or None
    return types, reason, action, bool(sep)


async def lock_cmd(update, context):
    if not await _gate(update, context):
        return
    types, reason, action, has_hash = _split(update)
    if not types:
        await ax_mod.say(update, "🔒 <b>Lock what?</b>\n<blockquote>Example: /lock sticker photo\nWith action: /lock invitelink ### no ads {ban}\nSee all: /locktypes</blockquote>")
        return
    if "all" in types and len(types) > 1:
        types = ["all"]
    bad = [t for t in types if t not in core.LOCK_TYPES]
    if bad:
        await ax_mod.say(update, f"💫 <b>Unknown lock</b>\n<blockquote>{esc(', '.join(bad))}\nSee /locktypes</blockquote>")
        return
    if action and ax_actions.parse_action(action)[0] is None:
        await ax_mod.say(update, "💫 <b>Unknown action</b>\n<blockquote>Use ban, mute, kick, warn, tmute 6h or tban 1d</blockquote>")
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        for t in types:
            if has_hash and not action and not reason:
                await c.execute("UPDATE ax_locks SET action=NULL, reason=NULL WHERE chat_id=$1 AND lock_type=$2", chat_id, t)
                continue
            await c.execute(
                "INSERT INTO ax_locks (chat_id, lock_type, action, reason) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (chat_id, lock_type) DO UPDATE SET action=$3, reason=$4", chat_id, t, action, reason)
    core.invalidate(chat_id)
    extra = f"\n⚔️ Action: <b>{esc(action)}</b>" if action else ""
    await ax_mod.say(update, f"🔒 <b>Locked</b>\n<blockquote>{esc(', '.join(types))}{extra}\nOnly admins and approved users can send these ✨</blockquote>")


async def unlock_cmd(update, context):
    if not await _gate(update, context):
        return
    types = [t.lower() for t in (context.args or [])]
    if not types:
        await ax_mod.say(update, "🔓 <b>Unlock what?</b>\n<blockquote>Example: /unlock sticker photo</blockquote>")
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        if "all" in types:
            await c.execute("DELETE FROM ax_locks WHERE chat_id=$1", chat_id)
            shown = "everything"
        else:
            await c.execute("DELETE FROM ax_locks WHERE chat_id=$1 AND lock_type = ANY($2::text[])", chat_id, types)
            shown = ", ".join(types)
    core.invalidate(chat_id)
    await ax_mod.say(update, f"🔓 <b>Unlocked</b>\n<blockquote>{esc(shown)} ✨</blockquote>")


async def locks_cmd(update, context):
    if not await _gate(update, context):
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        rows = await c.fetch("SELECT lock_type, action FROM ax_locks WHERE chat_id=$1 ORDER BY lock_type", chat_id)
    if not rows:
        await ax_mod.say(update, "🔓 <b>No locks</b>\n<blockquote>Nothing is locked here</blockquote>")
        return
    lines = "\n".join(f"🔒 {r['lock_type']}" + (f" → {esc(r['action'])}" if r["action"] else "") for r in rows)
    await ax_mod.say(update, f"🔒 <b>Locked in this group</b>\n<blockquote>{lines}</blockquote>")


async def locktypes_cmd(update, context):
    lines = "\n".join(f"• <code>{k}</code>: {v}" for k, v in core.LOCK_TYPES.items())
    await ax_mod.say(update, f"📋 <b>Lock types</b>\n<blockquote>{lines}</blockquote>")


async def lockwarns_cmd(update, context):
    if not await _gate(update, context):
        return
    chat_id = update.effective_chat.id
    arg = (context.args or [""])[0].lower()
    async with context.bot_data["pool"].acquire() as c:
        if arg in ("on", "yes"):
            val = True
        elif arg in ("off", "no"):
            val = False
        else:
            cur = await c.fetchval("SELECT lockwarns FROM ax_lock_settings WHERE chat_id=$1", chat_id) or False
            await ax_mod.say(update, f"⚙️ <b>Lock warns</b>\n<blockquote>Currently <b>{'on' if cur else 'off'}</b>\nUse /lockwarns on or off</blockquote>")
            return
        await c.execute("INSERT INTO ax_lock_settings (chat_id, lockwarns) VALUES ($1,$2) "
                        "ON CONFLICT (chat_id) DO UPDATE SET lockwarns=$2", chat_id, val)
    core.invalidate(chat_id)
    await ax_mod.say(update, f"✅ <b>Lock warns {'on' if val else 'off'}</b>\n<blockquote>{'Breaking a lock now adds a warning' if val else 'Locked messages are just deleted'}</blockquote>")


async def allowlist_cmd(update, context):
    if not await _gate(update, context):
        return
    chat_id = update.effective_chat.id
    args = [a.lower() for a in (context.args or [])]
    async with context.bot_data["pool"].acquire() as c:
        for a in args:
            await c.execute("INSERT INTO ax_allowlist (chat_id, item) VALUES ($1,$2) ON CONFLICT DO NOTHING", chat_id, a)
        rows = await c.fetch("SELECT item FROM ax_allowlist WHERE chat_id=$1 ORDER BY item", chat_id)
    core.invalidate(chat_id)
    lines = "\n".join(f"• {esc(r['item'])}" for r in rows) or "Empty"
    head = "✅ <b>Allowlist updated</b>" if args else "📋 <b>Allowlist</b>"
    await ax_mod.say(update, f"{head}\n<blockquote>{lines}</blockquote>")


async def rmallowlist_cmd(update, context):
    if not await _gate(update, context):
        return
    args = [a.lower() for a in (context.args or [])]
    if not args:
        await ax_mod.say(update, "💫 <b>Remove what?</b>\n<blockquote>Example: /rmallowlist @channel</blockquote>")
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_allowlist WHERE chat_id=$1 AND item = ANY($2::text[])", chat_id, args)
    core.invalidate(chat_id)
    await ax_mod.say(update, "✅ <b>Removed from allowlist</b>\n<blockquote>Locks apply to those items again</blockquote>")


async def rmallowlistall_cmd(update, context):
    if not await _gate(update, context):
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_allowlist WHERE chat_id=$1", chat_id)
    core.invalidate(chat_id)
    await ax_mod.say(update, "✅ <b>Allowlist cleared</b>\n<blockquote>Everything on it is checked again</blockquote>")


def register(app):
    core.register(app)
    app.add_handler(CommandHandler("lock", lock_cmd))
    app.add_handler(CommandHandler("unlock", unlock_cmd))
    app.add_handler(CommandHandler("locks", locks_cmd))
    app.add_handler(CommandHandler("locktypes", locktypes_cmd))
    app.add_handler(CommandHandler("lockwarns", lockwarns_cmd))
    app.add_handler(CommandHandler("allowlist", allowlist_cmd))
    app.add_handler(CommandHandler("rmallowlist", rmallowlist_cmd))
    app.add_handler(CommandHandler("rmallowlistall", rmallowlistall_cmd))

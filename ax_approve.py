"""Approvals: trusted users skip locks and blocklists.
/approve /unapprove /approved /unapproveall /approval"""
import time

from telegram import InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

import ax_mod
from ax_common import btn, esc, is_admin

_cache = {}  # (chat_id, user_id) -> (time, bool)


async def is_approved(pool, chat_id, user_id):
    key = (chat_id, user_id)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    async with pool.acquire() as c:
        ok = bool(await c.fetchval(
            "SELECT 1 FROM ax_approved WHERE chat_id=$1 AND user_id=$2", chat_id, user_id))
    _cache[key] = (time.time(), ok)
    return ok


async def _admin_gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


async def approve_cmd(update, context):
    target = await ax_mod.gate(update, context)
    if target is None:
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("INSERT INTO ax_approved (chat_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                        chat_id, target.id)
    _cache.pop((chat_id, target.id), None)
    kb = InlineKeyboardMarkup([[btn("❌ Unapprove", f"axa:un:{target.id}", style="danger")]])
    await ax_mod.say(update, ax_mod.card("✅", "Approved", target.first_name,
                                         "\n🔓 Locks and blocklists no longer apply"), kb)


async def unapprove_cmd(update, context):
    target = await ax_mod.gate(update, context, False)
    if target is None:
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_approved WHERE chat_id=$1 AND user_id=$2", chat_id, target.id)
    _cache.pop((chat_id, target.id), None)
    await ax_mod.say(update, ax_mod.card("🔒", "Unapproved", target.first_name,
                                         "\nRules apply to them again"))


async def approved_cmd(update, context):
    if not await _admin_gate(update, context):
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        rows = await c.fetch(
            "SELECT a.user_id, COALESCE(u.first_name, u.username, 'User') AS name "
            "FROM ax_approved a LEFT JOIN users u ON u.id = a.user_id "
            "WHERE a.chat_id=$1 ORDER BY name LIMIT 50", chat_id)
    if not rows:
        await ax_mod.say(update, "📋 <b>Approved users</b>\n<blockquote>Nobody is approved yet</blockquote>")
        return
    lines = "\n".join(f"• {esc(r['name'])} <code>{r['user_id']}</code>" for r in rows)
    await ax_mod.say(update, f"📋 <b>Approved users</b>\n<blockquote>{lines}</blockquote>")


async def unapproveall_cmd(update, context):
    if not await _admin_gate(update, context):
        return
    chat_id = update.effective_chat.id
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_approved WHERE chat_id=$1", chat_id)
    for k in [k for k in _cache if k[0] == chat_id]:
        _cache.pop(k, None)
    await ax_mod.say(update, "✅ <b>All approvals removed</b>\n<blockquote>Everyone follows the rules again 💜</blockquote>")


async def approval_cmd(update, context):
    chat = update.effective_chat
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside a group 💫</blockquote>")
        return
    target = await ax_mod.gate(update, context, False)
    if target is None:
        return
    ok = await is_approved(context.bot_data["pool"], chat.id, target.id)
    if ok:
        await ax_mod.say(update, ax_mod.card("✅", "Approved", target.first_name, "\nTrusted in this group"))
    else:
        await ax_mod.say(update, ax_mod.card("🔒", "Not approved", target.first_name, "\nRules apply to them"))


async def approve_callback(update, context):
    q = update.callback_query
    chat = update.effective_chat
    if not await is_admin(context.bot, chat.id, q.from_user.id):
        await q.answer("Admins only ✨", show_alert=True)
        return
    uid = int(q.data.split(":")[2])
    async with context.bot_data["pool"].acquire() as c:
        await c.execute("DELETE FROM ax_approved WHERE chat_id=$1 AND user_id=$2", chat.id, uid)
    _cache.pop((chat.id, uid), None)
    await q.answer("Unapproved ✅")
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


def register(app):
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("unapprove", unapprove_cmd))
    app.add_handler(CommandHandler("approved", approved_cmd))
    app.add_handler(CommandHandler("unapproveall", unapproveall_cmd))
    app.add_handler(CommandHandler("approval", approval_cmd))
    app.add_handler(CallbackQueryHandler(approve_callback, pattern="^axa:"))


"""Warn system with reasons, per-group limit, mode and expiry.
/warn /dwarn /swarn /warns /rmwarn (+ Remove-warn button)."""
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

import ax_mod
from ax_common import btn, esc, is_admin

log = logging.getLogger(__name__)
ICONS = {"ban": "🔨", "mute": "🔇", "kick": "👢", "tban": "🔨", "tmute": "🔇"}


async def get_settings(c, chat_id):
    row = await c.fetchrow("SELECT * FROM ax_warn_settings WHERE chat_id=$1", chat_id)
    if row:
        return dict(row)
    return {"warn_limit": 3, "warn_mode": "mute", "warn_mode_secs": 86400, "warn_expire_secs": 0}


async def count_active(c, chat_id, uid, expire_secs):
    if expire_secs > 0:
        return await c.fetchval(
            "SELECT COUNT(*) FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2 "
            "AND created_at > now() - make_interval(secs => $3)",
            chat_id, uid, float(expire_secs))
    return await c.fetchval("SELECT COUNT(*) FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2", chat_id, uid)


async def remove_latest(c, chat_id, uid):
    return await c.fetchval(
        "DELETE FROM ax_warn_log WHERE id = (SELECT id FROM ax_warn_log "
        "WHERE chat_id=$1 AND user_id=$2 ORDER BY id DESC LIMIT 1) RETURNING id", chat_id, uid)


async def punish(context, pool, chat_id, uid, mode, secs):
    if mode in ("tban", "tmute"):
        until = datetime.now(timezone.utc) + timedelta(seconds=secs)
        kind = "ban" if mode == "tban" else "mute"
        if kind == "mute":
            await context.bot.restrict_chat_member(chat_id, uid, permissions=ax_mod.MUTED, until_date=until)
        else:
            await context.bot.ban_chat_member(chat_id, uid, until_date=until)
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_temp_punish (chat_id, user_id, ptype, expires_at, reason) "
                "VALUES ($1,$2,$3,$4,'warn limit') ON CONFLICT (chat_id, user_id, ptype) "
                "DO UPDATE SET expires_at=$4", chat_id, uid, kind, until)
    else:
        await ax_mod.perform(mode, context.bot, pool, chat_id, uid)


def make_warn(mode):
    async def cmd(update, context):
        msg = update.effective_message
        if mode == "d" and not msg.reply_to_message:
            await ax_mod.say(update, "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want removed</blockquote>")
            return
        target = await ax_mod.gate(update, context)
        if target is None:
            return
        args = context.args or []
        d = args if msg.reply_to_message else args[1:]
        reason = " ".join(d) or None
        pool, chat_id = context.bot_data["pool"], update.effective_chat.id
        async with pool.acquire() as c:
            s = await get_settings(c, chat_id)
            await c.execute("INSERT INTO ax_warn_log (chat_id, user_id, reason) VALUES ($1,$2,$3)",
                            chat_id, target.id, reason)
            n = await count_active(c, chat_id, target.id, s["warn_expire_secs"])
        limit = s["warn_limit"]
        note = f"\n📝 {esc(reason)}" if reason else ""
        if n >= limit:
            try:
                await punish(context, pool, chat_id, target.id, s["warn_mode"], s["warn_mode_secs"])
            except Exception as e:
                await ax_mod.say(update, f"⚠️ <b>Couldn't punish</b>\n<blockquote>{esc(str(e))}</blockquote>")
                return
            async with pool.acquire() as c:
                await c.execute("DELETE FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2", chat_id, target.id)
            text = ax_mod.card(ICONS.get(s["warn_mode"], "⚠️"), "Warn limit reached", target.first_name,
                               f"\n📊 <b>{n}/{limit}</b> → <b>{s['warn_mode']}</b>{note}")
            kb = None
        else:
            text = ax_mod.card("⚠️", "Warned", target.first_name, f"\n📊 Warnings: <b>{n}/{limit}</b>{note}")
            kb = InlineKeyboardMarkup([[btn("➖ Remove warn", f"axw:rm:{target.id}", style="success")]])
        if mode == "d":
            for m in (msg.reply_to_message, msg):
                try:
                    await m.delete()
                except Exception:
                    pass
        elif mode == "s":
            try:
                await msg.delete()
            except Exception:
                pass
            return
        await ax_mod.say(update, text, kb)
    return cmd


async def warns_cmd(update, context):
    target = await ax_mod.gate(update, context, False)
    if target is None:
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    async with pool.acquire() as c:
        s = await get_settings(c, chat_id)
        rows = await c.fetch("SELECT reason, created_at FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2 "
                             "ORDER BY id DESC LIMIT 10", chat_id, target.id)
        n = await count_active(c, chat_id, target.id, s["warn_expire_secs"])
    if not rows:
        await ax_mod.say(update, ax_mod.card("✅", "Clean record", target.first_name, "\n📊 No warnings"))
        return
    lines = "\n".join(f"{i}. {esc(r['reason'] or 'No reason')}" for i, r in enumerate(rows, 1))
    await ax_mod.say(update, ax_mod.card("📋", "Warnings", target.first_name,
                                         f"\n📊 <b>{n}/{s['warn_limit']}</b> active\n{lines}"))


async def rmwarn_cmd(update, context):
    target = await ax_mod.gate(update, context, False)
    if target is None:
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    async with pool.acquire() as c:
        done = await remove_latest(c, chat_id, target.id)
        s = await get_settings(c, chat_id)
        n = await count_active(c, chat_id, target.id, s["warn_expire_secs"])
    if not done:
        await ax_mod.say(update, ax_mod.card("✅", "Nothing to remove", target.first_name, "\n📊 No warnings"))
        return
    await ax_mod.say(update, ax_mod.card("✅", "Warning removed", target.first_name,
                                         f"\n📊 Warnings: <b>{n}/{s['warn_limit']}</b>"))


async def warn_callback(update, context):
    q = update.callback_query
    chat = update.effective_chat
    if not await is_admin(context.bot, chat.id, q.from_user.id):
        await q.answer("Admins only ✨", show_alert=True)
        return
    uid = int(q.data.split(":")[2])
    async with context.bot_data["pool"].acquire() as c:
        done = await remove_latest(c, chat.id, uid)
    await q.answer("Warning removed ✅" if done else "No warnings left")
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


def register(app):
    app.add_handler(CommandHandler("warn", make_warn("n")))
    app.add_handler(CommandHandler("dwarn", make_warn("d")))
    app.add_handler(CommandHandler("swarn", make_warn("s")))
    app.add_handler(CommandHandler("warns", warns_cmd))
    app.add_handler(CommandHandler(["rmwarn", "unwarn"], rmwarn_cmd))
    app.add_handler(CallbackQueryHandler(warn_callback, pattern="^axw:"))

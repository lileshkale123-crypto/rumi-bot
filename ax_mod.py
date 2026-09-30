"""Moderation commands (Astraea-style) for Rumi."""
from telegram import ChatPermissions, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

from ax_common import btn, bot_is_admin, esc, is_admin, resolve_target

MAX_WARNS = 3
FULL = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
)
MUTED = ChatPermissions(can_send_messages=False)
META = {
    "mute": ("🔇", "Muted"), "unmute": ("🔊", "Unmuted"),
    "ban": ("🔨", "Banned"), "unban": ("✅", "Unbanned"),
    "kick": ("👢", "Kicked"),
}
UNDO = {"mute": ("unmute", "🔊 Unmute"), "ban": ("unban", "✅ Unban")}


def card(icon, title, name, extra=""):
    return f"{icon} <b>{title}</b>\n<blockquote>👤 <b>{esc(name)}</b>{extra}</blockquote>"


async def say(update, text, kb=None):
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def gate(update, context, protect=True):
    """Checks group, admin rights and target. Returns the target or None."""
    chat, user, bot = update.effective_chat, update.effective_user, context.bot
    if chat.type == "private":
        await say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return None
    if not await is_admin(bot, chat.id, user.id):
        await say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return None
    if not await bot_is_admin(bot, chat.id):
        await say(update, "⚠️ <b>I need admin rights</b>\n<blockquote>Promote me with ban and restrict permissions 💫</blockquote>")
        return None
    target = await resolve_target(update, context, context.bot_data["pool"])
    if target is None:
        await say(update, "💫 <b>Who?</b>\n<blockquote>Reply to someone, or use @username / user ID</blockquote>")
        return None
    if protect and (target.id == bot.id or await is_admin(bot, chat.id, target.id)):
        await say(update, "🛡️ <b>Protected</b>\n<blockquote>I can't act on admins ✨</blockquote>")
        return None
    return target


async def clear_timer(pool, chat_id, uid, ptype):
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_temp_punish WHERE chat_id=$1 AND user_id=$2 AND ptype=$3",
            chat_id, uid, ptype)


async def perform(kind, bot, pool, chat_id, uid):
    if kind == "mute":
        await bot.restrict_chat_member(chat_id, uid, permissions=MUTED)
    elif kind == "unmute":
        await bot.restrict_chat_member(chat_id, uid, permissions=FULL)
        await clear_timer(pool, chat_id, uid, "mute")
    elif kind == "ban":
        await bot.ban_chat_member(chat_id, uid)
    elif kind == "unban":
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
        await clear_timer(pool, chat_id, uid, "ban")
    elif kind == "kick":
        await bot.ban_chat_member(chat_id, uid)
        await bot.unban_chat_member(chat_id, uid)


def make_cmd(kind):
    async def cmd(update, context):
        target = await gate(update, context, kind in ("mute", "ban", "kick"))
        if target is None:
            return
        try:
            await perform(kind, context.bot, context.bot_data["pool"],
                          update.effective_chat.id, target.id)
        except Exception as e:
            await say(update, f"⚠️ <b>Couldn't do that</b>\n<blockquote>{esc(e)}</blockquote>")
            return
        icon, title = META[kind]
        kb = None
        if kind in UNDO:
            k, label = UNDO[kind]
            kb = InlineKeyboardMarkup([[btn(label, f"axm:{k}:{target.id}", style="success")]])
        await say(update, card(icon, title, target.first_name), kb)
    return cmd


async def mod_callback(update, context):
    q = update.callback_query
    _, kind, uid = q.data.split(":")
    chat = q.message.chat
    if not await is_admin(context.bot, chat.id, q.from_user.id):
        await q.answer("Admins only 🛡️", show_alert=True)
        return
    try:
        await perform(kind, context.bot, context.bot_data["pool"], chat.id, int(uid))
    except Exception as e:
        await q.answer(str(e)[:150], show_alert=True)
        return
    await q.answer("Done ✨")
    icon, title = META[kind]
    await q.edit_message_text(
        f"{icon} <b>{title}</b>\n<blockquote>By <b>{esc(q.from_user.first_name)}</b></blockquote>",
        parse_mode=ParseMode.HTML)


# ===== warn, promote, temp punishments, register =====
import re
from datetime import datetime, timedelta, timezone

DUR = re.compile(r"^(\d+)([smhd])$")


def parse_dur(t):
    m = DUR.match(t.lower())
    if not m:
        return None
    return int(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]


async def warn_cmd(update, context):
    target = await gate(update, context)
    if target is None:
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    async with pool.acquire() as c:
        n = await c.fetchval(
            "INSERT INTO ax_warns (chat_id, user_id, count) VALUES ($1,$2,1) "
            "ON CONFLICT (chat_id, user_id) DO UPDATE SET count = ax_warns.count + 1 "
            "RETURNING count", chat_id, target.id)
    if n >= MAX_WARNS:
        try:
            await perform("mute", context.bot, pool, chat_id, target.id)
        except Exception:
            pass
        async with pool.acquire() as c:
            await c.execute("UPDATE ax_warns SET count=0 WHERE chat_id=$1 AND user_id=$2", chat_id, target.id)
        await say(update, card("🔇", "Muted", target.first_name, f"\n⚠️ Reached {MAX_WARNS}/{MAX_WARNS} warnings"),
                  InlineKeyboardMarkup([[btn("🔊 Unmute", f"axm:unmute:{target.id}", style="success")]]))
    else:
        await say(update, card("⚠️", "Warned", target.first_name, f"\n📊 Warnings: <b>{n}/{MAX_WARNS}</b>"))


async def unwarn_cmd(update, context):
    target = await gate(update, context, False)
    if target is None:
        return
    async with context.bot_data["pool"].acquire() as c:
        n = await c.fetchval(
            "UPDATE ax_warns SET count = GREATEST(count-1,0) WHERE chat_id=$1 AND user_id=$2 RETURNING count",
            update.effective_chat.id, target.id)
    await say(update, card("✅", "Warning removed", target.first_name, f"\n📊 Warnings: <b>{n or 0}/{MAX_WARNS}</b>"))


async def promote_cmd(update, context):
    target = await gate(update, context, False)
    if target is None:
        return
    args = context.args or []
    parts = args if update.effective_message.reply_to_message else args[1:]
    title = " ".join(parts)[:16] or None
    try:
        await context.bot.promote_chat_member(update.effective_chat.id, target.id, can_restrict_members=True)
        if title:
            try:
                await context.bot.set_chat_administrator_custom_title(update.effective_chat.id, target.id, title)
            except Exception:
                pass
    except Exception as e:
        await say(update, f"⚠️ <b>Couldn't promote</b>\n<blockquote>{esc(e)}</blockquote>")
        return
    await say(update, card("⬆️", "Promoted", target.first_name, f"\n🏷️ {esc(title)}" if title else ""))


async def demote_cmd(update, context):
    target = await gate(update, context)
    if target is None:
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id, can_restrict_members=False, can_delete_messages=False,
            can_invite_users=False, can_pin_messages=False, can_change_info=False,
            can_manage_video_chats=False, can_promote_members=False)
    except Exception as e:
        await say(update, f"⚠️ <b>Couldn't demote</b>\n<blockquote>{esc(e)}</blockquote>")
        return
    await say(update, card("⬇️", "Demoted", target.first_name))


def make_temp(kind):
    async def cmd(update, context):
        target = await gate(update, context)
        if target is None:
            return
        args = context.args or []
        d = args if update.effective_message.reply_to_message else args[1:]
        secs = parse_dur(d[0]) if d else None
        if not secs:
            await say(update, f"⏳ <b>Add a time</b>\n<blockquote>Example: /t{kind} 30m spamming\nUnits: s, m, h, d</blockquote>")
            return
        reason = " ".join(d[1:]) or None
        until = datetime.now(timezone.utc) + timedelta(seconds=secs)
        chat_id = update.effective_chat.id
        try:
            if kind == "mute":
                await context.bot.restrict_chat_member(chat_id, target.id, permissions=MUTED, until_date=until)
            else:
                await context.bot.ban_chat_member(chat_id, target.id, until_date=until)
        except Exception as e:
            await say(update, f"⚠️ <b>Couldn't do that</b>\n<blockquote>{esc(e)}</blockquote>")
            return
        async with context.bot_data["pool"].acquire() as c:
            await c.execute(
                "INSERT INTO ax_temp_punish (chat_id, user_id, ptype, expires_at, reason) VALUES ($1,$2,$3,$4,$5) "
                "ON CONFLICT (chat_id, user_id, ptype) DO UPDATE SET expires_at=$4, reason=$5",
                chat_id, target.id, kind, until, reason)
        icon, title = META[kind]
        extra = f"\n⏳ For <b>{esc(d[0])}</b>" + (f"\n📝 {esc(reason)}" if reason else "")
        undo, label = UNDO[kind]
        await say(update, card(icon, title, target.first_name, extra),
                  InlineKeyboardMarkup([[btn(label, f"axm:{undo}:{target.id}", style="success")]]))
    return cmd


async def check_expired(context):
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT chat_id, user_id, ptype FROM ax_temp_punish WHERE expires_at <= now()")
    for r in rows:
        try:
            await perform("unmute" if r["ptype"] == "mute" else "unban", context.bot, pool, r["chat_id"], r["user_id"])
        except Exception:
            await clear_timer(pool, r["chat_id"], r["user_id"], r["ptype"])


def register(app):
    for name in ("mute", "unmute", "ban", "unban", "kick"):
        app.add_handler(CommandHandler(name, make_cmd(name)))
    app.add_handler(CommandHandler("tmute", make_temp("mute")))
    app.add_handler(CommandHandler("tban", make_temp("ban")))
    app.add_handler(CommandHandler("warn", warn_cmd))
    app.add_handler(CommandHandler("unwarn", unwarn_cmd))
    app.add_handler(CommandHandler("promote", promote_cmd))
    app.add_handler(CommandHandler("demote", demote_cmd))
    app.add_handler(CallbackQueryHandler(mod_callback, pattern="^axm:"))
    app.job_queue.run_repeating(check_expired, interval=30, first=15)

"""AFK status and /info."""
import time
from datetime import datetime, timezone

from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from ax_common import esc, find_user_by_username, resolve_target

H = ParseMode.HTML
_notice = {}


def dur(start):
    s = int((datetime.now(timezone.utc) - start).total_seconds())
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    parts = ([f"{h}h"] if h else []) + ([f"{m}m"] if m else []) + [f"{sec}s"]
    return " ".join(parts)


async def afk_cmd(update, context):
    user, msg = update.effective_user, update.effective_message
    pool = context.bot_data["pool"]
    arg = " ".join(context.args or []).strip()
    if arg.lower() == "off":
        async with pool.acquire() as c:
            r = await c.execute("DELETE FROM ax_afk WHERE user_id=$1", user.id)
        await msg.reply_text(
            "✅ <b>AFK removed</b>" if r.endswith("1") else "💫 <b>You're not AFK</b>", parse_mode=H)
        return
    reason = arg[:200] or None
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_afk (user_id, reason) VALUES ($1,$2) ON CONFLICT (user_id) "
            "DO UPDATE SET reason=$2, start_time=now(), msg_count=0", user.id, reason)
    extra = f"\n📝 {esc(reason)}" if reason else ""
    await msg.reply_text(
        f"💤 <b>AFK on</b>\n<blockquote>👤 <b>{esc(user.first_name)}</b> is away{extra}</blockquote>",
        parse_mode=H)


async def afk_watcher(update, context):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or user.is_bot:
        return
    pool = context.bot_data["pool"]
    raw = msg.text or msg.caption or ""
    if not raw.lower().startswith("/afk"):
        async with pool.acquire() as c:
            r = await c.fetchrow(
                "DELETE FROM ax_afk WHERE user_id=$1 RETURNING reason, start_time, msg_count", user.id)
        if r:
            extra = f"\n📝 {esc(r['reason'])}" if r["reason"] else ""
            await msg.reply_text(
                f"👋 <b>Welcome back, {esc(user.first_name)}!</b>\n<blockquote>"
                f"⏳ Away for <b>{dur(r['start_time'])}</b>{extra}\n"
                f"💌 Mentions while away: <b>{r['msg_count']}</b></blockquote>", parse_mode=H)
    targets = set()
    rp = msg.reply_to_message
    if rp and rp.from_user and not rp.from_user.is_bot:
        targets.add(rp.from_user.id)
    su = getattr(getattr(msg, "forward_origin", None), "sender_user", None)
    if su and not su.is_bot:
        targets.add(su.id)
    for e in (msg.entities or msg.caption_entities or []):
        if e.type == "text_mention" and e.user and not e.user.is_bot:
            targets.add(e.user.id)
        elif e.type == "mention":
            name = (msg.parse_entity(e) if msg.text else msg.parse_caption_entity(e)).lstrip("@")
            row = await find_user_by_username(pool, name)
            if row:
                targets.add(row["id"])
    now = time.time()
    for tid in targets - {user.id}:
        async with pool.acquire() as c:
            r = await c.fetchrow(
                "UPDATE ax_afk SET msg_count = msg_count + 1 WHERE user_id=$1 "
                "RETURNING reason, start_time, msg_count", tid)
            if not r:
                continue
            first = await c.fetchval("SELECT first_name FROM users WHERE id=$1", tid)
        key = (update.effective_chat.id, tid)
        if now - _notice.get(key, 0) < 15:
            continue
        _notice[key] = now
        extra = f"\n📝 {esc(r['reason'])}" if r["reason"] else ""
        await msg.reply_text(
            f"💤 <b>{esc(first or 'This user')} is AFK</b>\n<blockquote>"
            f"⏳ Away for <b>{dur(r['start_time'])}</b>{extra}</blockquote>", parse_mode=H)


# ===== /info and register =====
import config
import ui

ROLES = {"creator": "👑 Owner", "administrator": "🛡️ Admin", "member": "👤 Member",
         "restricted": "🚫 Restricted", "left": "🚪 Left", "kicked": "⛔ Banned"}


def ts(t):
    return t.strftime("%d %b %Y, %H:%M UTC") if t else "—"


async def info_cmd(update, context):
    msg, chat = update.effective_message, update.effective_chat
    pool = context.bot_data["pool"]
    target = await resolve_target(update, context, pool)
    if target is None:
        if context.args:
            await msg.reply_text(
                "🔎 <b>Not found</b>\n<blockquote>I haven't seen that person yet 💫</blockquote>", parse_mode=H)
            return
        target = update.effective_user
    user, role, title = target, "—", None
    if chat.type != "private":
        try:
            m = await context.bot.get_chat_member(chat.id, target.id)
            user, role = m.user, ROLES.get(m.status, "—")
            title = getattr(m, "custom_title", None)
        except Exception:
            pass
    async with pool.acquire() as c:
        st = await c.fetchrow(
            "SELECT first_seen, last_seen, message_count FROM ax_user_stats WHERE user_id=$1", target.id)
        w = await c.fetchrow("SELECT level, xp, cash, gems FROM wallets WHERE user_id=$1", target.id)
        afk = await c.fetchrow("SELECT reason, start_time FROM ax_afk WHERE user_id=$1", target.id)
    first = getattr(user, "first_name", None) or "User"
    name = esc(first + (" " + user.last_name if getattr(user, "last_name", None) else ""))
    uname = f"@{esc(user.username)}" if getattr(user, "username", None) else "—"
    kind = "🤖 Bot" if getattr(user, "is_bot", False) else "🧑 Human"
    text = (
        f"🪪 <b>User Info</b>\n<blockquote>"
        f"👤 <b>{name}</b>\n🔗 {uname}\n🆔 <code>{target.id}</code>\n{kind}\n"
        f"🎭 {role}" + (f" · 🏷️ {esc(title)}" if title else "") + "</blockquote>\n"
    )
    if w:
        text += (f"🌙 <b>Rumi Profile</b>\n<blockquote>⭐ Level <b>{w['level']}</b> · ✨ {w['xp']} XP\n"
                 f"💰 {config.CURRENCY_SYMBOL}{ui.format_number(w['cash'])} · 💎 {w['gems']}</blockquote>\n")
    if st:
        text += (f"📊 <b>Activity</b>\n<blockquote>💬 Messages: <b>{st['message_count']}</b>\n"
                 f"🌱 First seen: {ts(st['first_seen'])}\n🕒 Last seen: {ts(st['last_seen'])}</blockquote>\n")
    if afk:
        why = f" · {esc(afk['reason'])}" if afk["reason"] else ""
        text += f"💤 <b>AFK</b> for {dur(afk['start_time'])}{why}"
    else:
        text += "🟢 <b>Active</b>"
    await msg.reply_text(text, parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("afk", afk_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.PRIVATE, afk_watcher), group=-2)

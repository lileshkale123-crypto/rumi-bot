"""Captcha for new joiners: mute until solved, kick on timeout."""
import random
from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from ax_common import btn, esc
import ax_approve
import ax_rules

SOLVED = ChatPermissions(
    can_send_messages=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
    can_invite_users=True)
MUTED = ChatPermissions(can_send_messages=False)


async def settings(pool, chat_id):
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT * FROM ax_captcha_settings WHERE chat_id=$1", chat_id)
    return dict(r) if r else {
        "enabled": False, "mode": "button", "mute_secs": 0,
        "kick": True, "kick_secs": 300, "rules": False, "btn_text": None}


def challenge(mode):
    if mode == "math":
        a, b = random.randint(2, 9), random.randint(2, 9)
        return f"{a} + {b} = ?", str(a + b)
    if mode == "text":
        w = "".join(random.choice("ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(4))
        return f"Type this code: <code>{w}</code>", w
    return None, None


async def join(update, context):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not msg.new_chat_members:
        return
    pool = context.bot_data["pool"]
    s = await settings(pool, chat.id)
    if not s["enabled"]:
        return
    for m in msg.new_chat_members:
        if m.is_bot or m.id == context.bot.id:
            continue
        if await ax_approve.is_approved(pool, chat.id, m.id):
            continue
        try:
            await context.bot.restrict_chat_member(chat.id, m.id, MUTED)
        except Exception as e:
            __import__("logging").warning("CAPTCHA mute failed: %r", e)
            return
        try:
            q, ans = challenge(s["mode"])
            name = esc(m.first_name or "friend")
            rows = []
            if q:
                body = (f"🔐 <b>Verify, {name}</b>\n<blockquote>{q}\n"
                        f"Send the answer here ✨</blockquote>")
            else:
                body = (f"🔐 <b>Welcome, {name}</b>\n<blockquote>Tap the "
                        f"button to prove you are human ✨</blockquote>")
                label = s["btn_text"] or "✅ I am human"
                rows.append([btn(label, f"axc:{m.id}")])
            if s.get("rules") and context.bot.username:
                r_cfg = await ax_rules.get(pool, chat.id)
                r_label = r_cfg.get("button_text") or "📜 Rules"
                rows.append([btn(r_label, url=f"https://t.me/{context.bot.username}?start=rules_{chat.id}")])
            kb = InlineKeyboardMarkup(rows) if rows else None
            sent = await context.bot.send_message(
                chat.id, body, parse_mode=ParseMode.HTML, reply_markup=kb)
            exp = datetime.now(timezone.utc) + timedelta(seconds=s["kick_secs"])
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ax_captcha_pending VALUES ($1,$2,$3,$4,$5) "
                    "ON CONFLICT (chat_id,user_id) DO UPDATE SET answer=$3, "
                    "msg_id=$4, expires=$5", chat.id, m.id, ans,
                    sent.message_id, exp)
            NEED[0] = True
        except Exception:
            __import__("logging").exception("CAPTCHA send failed")


async def finish(context, chat_id, user_id, s):
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "DELETE FROM ax_captcha_pending WHERE chat_id=$1 AND "
            "user_id=$2 RETURNING msg_id", chat_id, user_id)
    if not r:
        return False
    try:
        if s["mute_secs"] > 0:
            until = datetime.now(timezone.utc) + timedelta(
                seconds=s["mute_secs"])
            await context.bot.restrict_chat_member(
                chat_id, user_id, MUTED, until_date=until)
        else:
            await context.bot.restrict_chat_member(chat_id, user_id, SOLVED)
        await context.bot.delete_message(chat_id, r["msg_id"])
    except Exception:
        pass
    return True


async def press(update, context):
    q = update.callback_query
    uid = int(q.data.split(":")[1])
    if q.from_user.id != uid:
        await q.answer("This button is not for you 🌸", show_alert=True)
        return
    s = await settings(context.bot_data["pool"], q.message.chat.id)
    ok = await finish(context, q.message.chat.id, uid, s)
    await q.answer("Verified, welcome ✨" if ok else "Already done")


async def answer(update, context):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not msg.text or chat.type == "private":
        return
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT answer FROM ax_captcha_pending WHERE chat_id=$1 AND "
            "user_id=$2", chat.id, update.effective_user.id)
    if not r or not r["answer"]:
        return
    try:
        await msg.delete()
    except Exception:
        pass
    if msg.text.strip().upper() == r["answer"].upper():
        s = await settings(pool, chat.id)
        await finish(context, chat.id, update.effective_user.id, s)


NEED = [True]


async def expire_job(context):
    if not NEED[0]:
        return
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        rows = await c.fetch(
            "DELETE FROM ax_captcha_pending WHERE expires < NOW() "
            "RETURNING chat_id, user_id, msg_id")
    for r in rows:
        s = await settings(pool, r["chat_id"])
        try:
            await context.bot.delete_message(r["chat_id"], r["msg_id"])
            if s["kick"]:
                await context.bot.ban_chat_member(r["chat_id"], r["user_id"])
                await context.bot.unban_chat_member(r["chat_id"], r["user_id"])
        except Exception:
            pass
    async with pool.acquire() as c:
        if not await c.fetchval("SELECT count(*) FROM ax_captcha_pending"):
            NEED[0] = False


def register(app):
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, join), group=-2)
    app.add_handler(CallbackQueryHandler(press, pattern=r"^axc:\d+$"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, answer),
        group=-1)
    app.job_queue.run_repeating(expire_job, interval=60, first=20)

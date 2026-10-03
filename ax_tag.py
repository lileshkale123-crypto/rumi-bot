"""/tagall (admins only) and /admins (anyone)."""
import asyncio
import html
import logging
import time

from telegram.ext import CommandHandler

from ax_common import is_admin

log = logging.getLogger(__name__)

MAX_TAG = 200
BATCH = 5
TAGALL_COOLDOWN = 300
ADMINS_COOLDOWN = 30
_last = {}


def _mention(uid, name):
    return f'<a href="tg://user?id={uid}">{html.escape(name or "friend")}</a>'


def _left(key, secs):
    return secs - (time.monotonic() - _last.get(key, -1e9))


async def _send_batches(bot, chat_id, header, people):
    for i in range(0, len(people), BATCH):
        line = " ".join(_mention(u, n) for u, n in people[i:i + BATCH])
        text = f"{header}\n\n{line}" if i == 0 else line
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            log.exception("tag batch failed")
        await asyncio.sleep(1.5)


async def tagall_cmd(update, context):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup"):
        await msg.reply_html("⚠️ This command works only in groups.")
        return
    anon = msg.sender_chat and msg.sender_chat.id == chat.id
    if not anon and not await is_admin(context.bot, chat.id, user.id):
        await msg.reply_html("🔒 <b>Only admins</b> can use /tagall.")
        return
    left = _left(("all", chat.id), TAGALL_COOLDOWN)
    if left > 0:
        await msg.reply_html(f"⏳ <b>Easy there!</b> Try again in {int(left) + 1}s.")
        return
    _last[("all", chat.id)] = time.monotonic()

    pool = context.bot_data["pool"]
    people = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT u.id, u.first_name FROM ax_chat_members m "
            "JOIN users u ON u.id = m.user_id WHERE m.chat_id = $1 LIMIT 500",
            chat.id,
        )
    for r in rows:
        people[r["id"]] = r["first_name"]
    try:
        for a in await context.bot.get_chat_administrators(chat.id):
            if not a.user.is_bot:
                people[a.user.id] = a.user.first_name
    except Exception:
        log.exception("get admins failed")
    items = list(people.items())[:MAX_TAG]
    if not items:
        await msg.reply_html("😿 I don't know anyone here yet.")
        return
    note = html.escape(" ".join(context.args)[:200]) or "Everyone, please check in! 🌙"
    header = f"📣 <b>ATTENTION</b>\n<blockquote>{note}</blockquote>"
    context.application.create_task(_send_batches(context.bot, chat.id, header, items))


async def admins_cmd(update, context):
    msg, chat = update.effective_message, update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await msg.reply_html("⚠️ This command works only in groups.")
        return
    left = _left(("adm", chat.id), ADMINS_COOLDOWN)
    if left > 0:
        await msg.reply_html(f"⏳ Admins were just called. Wait {int(left) + 1}s.")
        return
    _last[("adm", chat.id)] = time.monotonic()
    try:
        admins = [(a.user.id, a.user.first_name) for a in await context.bot.get_chat_administrators(chat.id) if not a.user.is_bot]
    except Exception:
        await msg.reply_html("😿 I couldn't fetch the admins right now.")
        return
    note = html.escape(" ".join(context.args)[:200]) or "Someone needs help!"
    line = " ".join(_mention(u, n) for u, n in admins)
    await msg.reply_html(f"🚨 <b>CALLING ADMINS</b>\n<blockquote>{note}</blockquote>\n\n{line}")


def register(app):
    app.add_handler(CommandHandler("tagall", tagall_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))

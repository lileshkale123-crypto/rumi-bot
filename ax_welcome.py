"""Welcome and goodbye messages."""
import os

from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from ax_common import esc, is_admin

IMG = "assets/rumi-start-v2.png"
DEFAULTS = {
    "welcome": "✨ <b>Welcome, {first_name}!</b>\n<blockquote>Glad to have you in <b>{chat_name}</b> 💫\nBe kind and enjoy your stay 🌸</blockquote>",
    "goodbye": "👋 <b>Goodbye, {first_name}</b>\n<blockquote>See you again in <b>{chat_name}</b> 💫</blockquote>",
}


async def get(pool, chat_id, kind):
    async with pool.acquire() as c:
        r = await c.fetchrow(
            f"SELECT {kind}_text t, {kind}_enabled e, {kind}_photo p FROM ax_settings WHERE chat_id=$1", chat_id)
    return (r["t"], r["e"], r["p"]) if r else (None, True, None)


async def put(pool, chat_id, kind, col, value):
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_settings (chat_id, {kind}_{col}) VALUES ($1,$2) "
            f"ON CONFLICT (chat_id) DO UPDATE SET {kind}_{col}=$2", chat_id, value)


def make_set(kind):
    async def cmd(update, context):
        msg, chat = update.effective_message, update.effective_chat
        html = lambda t: msg.reply_text(t, parse_mode=ParseMode.HTML)
        if chat.type == "private" or not await is_admin(context.bot, chat.id, update.effective_user.id):
            await html("🛡️ <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>")
            return
        pool = context.bot_data["pool"]
        parts = (msg.text or msg.caption or "").split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        rp = msg.reply_to_message
        photo = msg.photo[-1] if msg.photo else (rp.photo[-1] if rp and rp.photo else None)
        low = arg.lower()
        if low in ("on", "off"):
            await put(pool, chat.id, kind, "enabled", low == "on")
            await html(f"{'✅' if low == 'on' else '🚫'} <b>{kind.title()} {low}</b>")
        elif low == "reset":
            await put(pool, chat.id, kind, "text", None)
            await put(pool, chat.id, kind, "photo", None)
            await html(f"🔄 <b>{kind.title()} reset</b>\n<blockquote>Back to the default ✨</blockquote>")
        elif not arg and not photo:
            t, e, p = await get(pool, chat.id, kind)
            await html(
                f"📖 <b>{kind.title()} settings</b>\n<blockquote>Status: <b>{'on' if e else 'off'}</b>\n"
                f"Text: <b>{'custom' if t else 'default'}</b>\nPhoto: <b>{'custom' if p else 'default'}</b></blockquote>\n"
                f"<b>Usage</b>\n<blockquote>/set{kind} your text\nSend a photo with that caption\n"
                f"Reply to a photo with /set{kind}\n/set{kind} on | off | reset\n"
                f"Tags: {{first_name}} {{chat_name}}</blockquote>")
        else:
            done = []
            if arg:
                await put(pool, chat.id, kind, "text", arg)
                done.append("text")
            if photo:
                await put(pool, chat.id, kind, "photo", photo.file_id)
                done.append("photo")
            await put(pool, chat.id, kind, "enabled", True)
            await html(f"✅ <b>{kind.title()} updated</b>\n<blockquote>{' + '.join(done)} saved 💫</blockquote>")
    return cmd


async def announce(context, chat, kind, member):
    if member.id == context.bot.id:
        return
    text, enabled, photo = await get(context.bot_data["pool"], chat.id, kind)
    if not enabled:
        return
    tpl = text or DEFAULTS[kind]
    try:
        body = tpl.format(first_name=esc(member.first_name), chat_name=esc(chat.title or "this group"))
    except Exception:
        body = tpl
    kw = {"parse_mode": ParseMode.HTML}
    try:
        if photo:
            await context.bot.send_photo(chat.id, photo, caption=body, **kw)
        elif kind == "welcome" and os.path.exists(IMG):
            with open(IMG, "rb") as f:
                await context.bot.send_photo(chat.id, f, caption=body, **kw)
        else:
            await context.bot.send_message(chat.id, body, **kw)
    except Exception:
        await context.bot.send_message(chat.id, body)


async def on_join(update, context):
    for m in update.effective_message.new_chat_members or []:
        await announce(context, update.effective_chat, "welcome", m)


async def on_leave(update, context):
    m = update.effective_message.left_chat_member
    if m:
        await announce(context, update.effective_chat, "goodbye", m)


def register(app):
    for kind in ("welcome", "goodbye"):
        app.add_handler(CommandHandler(f"set{kind}", make_set(kind)))
        app.add_handler(MessageHandler(
            filters.PHOTO & filters.CaptionRegex(rf"(?i)^/set{kind}(\s|$)"), make_set(kind)))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_join))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_leave))

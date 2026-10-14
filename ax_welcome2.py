"""Rose-style welcome/goodbye extra commands."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import is_admin
from ax_welcome import get, put, DEFAULTS


async def _admin(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private" or not await is_admin(
            context.bot, chat.id, user.id):
        await update.effective_message.reply_text(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group "
            "as an admin ✨</blockquote>", parse_mode=ParseMode.HTML)
        return False
    return True


def _yn(arg):
    a = (arg or "").lower()
    if a in ("on", "yes", "y", "true"):
        return True
    if a in ("off", "no", "n", "false"):
        return False
    return None


def make_toggle(kind):
    async def cmd(update, context):
        if not await _admin(update, context):
            return
        msg, chat = update.effective_message, update.effective_chat
        pool = context.bot_data["pool"]
        v = _yn(context.args[0] if context.args else "")
        if v is None:
            t, e, p = await get(pool, chat.id, kind)
            await msg.reply_text(
                f"📖 <b>{kind.title()}</b>\n<blockquote>Status: "
                f"<b>{'on' if e else 'off'}</b>\nText: "
                f"<b>{'custom' if t else 'default'}</b>\n"
                f"Use /{kind} on or off</blockquote>",
                parse_mode=ParseMode.HTML)
            return
        await put(pool, chat.id, kind, "enabled", v)
        await msg.reply_text(
            f"{'✅' if v else '🚫'} <b>{kind.title()} "
            f"{'on' if v else 'off'}</b>", parse_mode=ParseMode.HTML)
    return cmd


def make_reset(kind):
    async def cmd(update, context):
        if not await _admin(update, context):
            return
        chat = update.effective_chat
        pool = context.bot_data["pool"]
        await put(pool, chat.id, kind, "text", None)
        await put(pool, chat.id, kind, "photo", None)
        await update.effective_message.reply_text(
            f"🔄 <b>{kind.title()} reset</b>\n<blockquote>Back to the "
            f"default ✨</blockquote>", parse_mode=ParseMode.HTML)
    return cmd


async def cleanwelcome(update, context):
    if not await _admin(update, context):
        return
    msg, chat = update.effective_message, update.effective_chat
    v = _yn(context.args[0] if context.args else "")
    async with context.bot_data["pool"].acquire() as c:
        if v is None:
            r = await c.fetchrow(
                "SELECT clean_welcome FROM ax_settings WHERE chat_id=$1",
                chat.id)
            on = bool(r and r["clean_welcome"])
            await msg.reply_text(
                f"🧹 <b>Clean welcome</b>\n<blockquote>Status: "
                f"<b>{'on' if on else 'off'}</b>\nUse /cleanwelcome on "
                f"or off</blockquote>", parse_mode=ParseMode.HTML)
            return
        await c.execute(
            "INSERT INTO ax_settings (chat_id, clean_welcome) VALUES "
            "($1,$2) ON CONFLICT (chat_id) DO UPDATE SET "
            "clean_welcome=$2", chat.id, v)
    await msg.reply_text(
        f"🧹 <b>Clean welcome {'on' if v else 'off'}</b>\n<blockquote>"
        f"{'Old welcome is deleted when a new one is sent' if v else 'Old welcomes stay'}"
        f" ✨</blockquote>", parse_mode=ParseMode.HTML)


async def welcomehelp(update, context):
    await update.effective_message.reply_text(
        "📖 <b>Welcome fillings</b>\n<blockquote>"
        "{first} {last} {fullname} {username} {mention} {id} {chatname}\n"
        "{preview} {nonotif} {protect} {mediaspoiler}\n"
        "Random: split replies with %%%\n"
        "Button: [text](buttonurl://https://example.com)\n"
        "Same row: add :same after the url</blockquote>",
        parse_mode=ParseMode.HTML)


def register(app):
    for kind in ("welcome", "goodbye"):
        app.add_handler(CommandHandler(kind, make_toggle(kind)))
        app.add_handler(CommandHandler(f"reset{kind}", make_reset(kind)))
    app.add_handler(CommandHandler("cleanwelcome", cleanwelcome))
    app.add_handler(CommandHandler("welcomehelp", welcomehelp))

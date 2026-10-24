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
        if context.args and context.args[0].lower() == "noformat":
            t, e, p = await get(pool, chat.id, kind)
            await msg.reply_text(t or DEFAULTS[kind])
            return
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
    r = update.effective_message.reply_text
    H = ParseMode.HTML
    await r(
        "👋 <b>Welcome guide 1/3: the basics</b>\n"
        "<blockquote>/setwelcome your text sets the message.\n"
        "Send a photo with /setwelcome as caption to add a picture.\n"
        "/welcome on or off turns it on or off.\n"
        "/resetwelcome goes back to the default.\n"
        "/cleanwelcome on deletes the old welcome when a new one comes.</blockquote>\n"
        "<b>Fillings</b> are words in curly brackets that change per person:\n"
        "<blockquote>{first} first name\n{last} last name\n"
        "{fullname} both names\n{username} @username, or name if none\n"
        "{mention} a tap-able name\n{id} their id\n{chatname} group name</blockquote>"
        "Example: <code>/setwelcome Hi {first}, welcome to {chatname}!</code>\n"
        "Shows: <i>Hi Asha, welcome to My Group!</i>", parse_mode=H)
    await r(
        "🎛 <b>Welcome guide 2/3: buttons</b>\n"
        "<blockquote>Format: [Text](buttonurl://link)\n"
        "Put it at the end of your welcome text.</blockquote>\n"
        "<b>Same row:</b> add <code>:same</code> to put a button beside the "
        "one before it.\n"
        "<b>Colours:</b> telegram allows only three, plus normal:\n"
        "<blockquote>:blue 🔵 main action\n:green 🟢 good or safe\n"
        ":red 🔴 warning\nno word = normal grey-tinted button</blockquote>"
        "Colours and :same can be combined in any order.\n"
        "Other colours (brown, purple...) are not possible, telegram decides.",
        parse_mode=H)
    await r(
        "✨ <b>Welcome guide 3/3: examples</b>\n"
        "<b>One blue button:</b>\n"
        "<code>Welcome {first}!\n"
        "[Our site](buttonurl://https://example.com:blue)</code>\n\n"
        "<b>Two buttons on one row:</b>\n"
        "<code>[Google](buttonurl://https://google.com:green)\n"
        "[Bing](buttonurl://https://bing.com:same:red)</code>\n\n"
        "<b>Random welcome</b> (bot picks one each time, split with %%%):\n"
        "<code>Hello {first}! %%% Hey {first}, glad you came! "
        "%%% Welcome {mention}!</code>\n\n"
        "<b>Extras:</b> {preview} shows link previews, {nonotif} sends "
        "silently, {protect} blocks forwarding, {mediaspoiler} hides the "
        "photo until tapped.", parse_mode=H)


def register(app):
    for kind in ("welcome", "goodbye"):
        app.add_handler(CommandHandler(kind, make_toggle(kind)))
        app.add_handler(CommandHandler(f"reset{kind}", make_reset(kind)))
    app.add_handler(CommandHandler("cleanwelcome", cleanwelcome))
    app.add_handler(CommandHandler("welcomehelp", welcomehelp))


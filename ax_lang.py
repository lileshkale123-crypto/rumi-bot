"""Language choice: /setlang (English, Hindi)."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

from ax_common import esc

H = ParseMode.HTML
LANGS = {"en": "English", "hi": "हिन्दी"}

TXT = {
    "en": {
        "pick": "🌐 <b>Choose your language</b>\n<blockquote>%s, pick the language you like. Your choice is saved just for you 💫</blockquote>",
        "done": "✅ <b>Language saved</b>\n<blockquote>%s, Rumi will use English with you ✨</blockquote>",
    },
    "hi": {
        "pick": "🌐 <b>अपनी भाषा चुनें</b>\n<blockquote>%s, अपनी पसंद की भाषा चुनिए। आपकी पसंद सिर्फ आपके लिए सेव होती है 💫</blockquote>",
        "done": "✅ <b>भाषा सेव हो गई</b>\n<blockquote>%s, अब Rumi आपसे हिन्दी में बात करेगी ✨</blockquote>",
    },
}


async def get_lang(pool, user_id):
    try:
        async with pool.acquire() as c:
            v = await c.fetchval(
                "SELECT lang FROM ax_user_lang WHERE user_id=$1", user_id)
    except Exception:
        return "en"
    return v if v in LANGS else "en"


async def setlang_cmd(update, context):
    user = update.effective_user
    cur = await get_lang(context.bot_data["pool"], user.id)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(name, callback_data="axlg_" + code)
        for code, name in LANGS.items()]])
    await update.effective_message.reply_text(
        TXT[cur]["pick"] % esc(user.first_name or "friend"),
        parse_mode=H, reply_markup=kb)


async def setlang_cb(update, context):
    q = update.callback_query
    code = q.data[5:]
    if code not in LANGS:
        await q.answer()
        return
    async with context.bot_data["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO ax_user_lang (user_id, lang) VALUES ($1,$2) "
            "ON CONFLICT (user_id) DO UPDATE SET lang=$2",
            q.from_user.id, code)
    await q.answer()
    await q.edit_message_text(
        TXT[code]["done"] % esc(q.from_user.first_name or "friend"),
        parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(CallbackQueryHandler(setlang_cb, pattern="^axlg_"))

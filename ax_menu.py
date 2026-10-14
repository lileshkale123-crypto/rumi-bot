"""/features menu for the new group tools."""
from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

from ax_common import btn

H = ParseMode.HTML
PAGES = {
    "set": ("⚙️", "Settings", "/settings: open the menu\n/setflood 6: anti-spam\n"
            "/setwelcome text or photo\n/setgoodbye text or photo\n"
            "Tags: {first_name} {chat_name}"),
    "notes": ("📒", "Notes & Filters", "/save name text: save a note\n/get name\n/notes\n"
              "/clear name\n/clearallnotes\n\n/filter word reply: auto-reply\n"
              "/filters\n/stop word\n/clearfilters\n\nReply to any media with /save or /filter."),
    "fun": ("💞", "Fun & Social", "/ship: random pair, +10 XP each\n/match: reply to someone\n"
            "/couple: couple of the day, $500 each\n/q: reply to make a quote sticker"),
    "you": ("🪪", "Profile & Status", "/info: yourself, or a reply or @username\n"
            "/afk reason: mark yourself away\n/afk off"),
}


PAGES["chat"] = ("💬", "Chat and Economy",
    "Say rumi or reply to my message and I'll chat with you.\n"
    "I also react to messages with emojis.\n\n"
    "/start: your snapshot and games\n"
    "/wallet /claim /pay /guard /rob /rankings\n"
    "/vault /deposit /withdraw /work /spin\n"
    "/bluff /mafia /stopmafia /mafiatop")

PAGES["more"] = ("\u2728", "More Commands",
    "/clan: build or join a Clan\n"
    "/topgroups: see the top groups\n"
    "/tagall: mention everyone (admins)\n"
    "/admins: call the group admins\n"
    "/speak: Rumi speaks\n"
    "/kang: take a sticker for yourself\n"
    "/id: get a user or chat ID\n"
    "/ping: check if Rumi is awake\n"
    "/setlang: choose English or Hindi\n"
    "/privacy: what Rumi keeps\n"
    "/donate: support Rumi\n"
    "/modhelp: full moderation guide")


def home():
    text = ("✨ <b>Rumi Group Tools</b>\n<blockquote>Pick a category to see what I can do 💫\n"
            "Economy and games are in the Chat page 🌙</blockquote>")
    b = [btn(f"{i} {t}", f"axh:{k}", style="primary") for k, (i, t, _) in PAGES.items()]
    b.insert(0, btn("🛡️ Moderation", "axhm:home", style="primary"))
    kb = InlineKeyboardMarkup([*[b[i:i+2] for i in range(0, len(b), 2)], [btn("✖ Close", "axh:close", style="danger")]])
    return text, kb


def page(key):
    i, t, body = PAGES[key]
    kb = InlineKeyboardMarkup([[btn("⬅ Back", "axh:home"), btn("✖ Close", "axh:close", style="danger")]])
    return f"{i} <b>{t}</b>\n<blockquote>{body}</blockquote>", kb


async def features_cmd(update, context):
    text, kb = home()
    await update.effective_message.reply_text(text, parse_mode=H, reply_markup=kb)


async def menu_cb(update, context):
    q = update.callback_query
    key = q.data.split(":")[1]
    await q.answer()
    if key == "close":
        await q.message.delete()
        return
    text, kb = home() if key == "home" else page(key)
    try:
        await q.edit_message_text(text, parse_mode=H, reply_markup=kb)
    except Exception:
        pass


async def start_menu_cb(update, context):
    q = update.callback_query
    await q.answer()
    text, kb = home()
    await q.message.reply_text(text, parse_mode=H, reply_markup=kb)


def register(app):
    app.add_handler(CommandHandler("features", features_cmd))
    app.add_handler(CallbackQueryHandler(menu_cb, pattern="^axh:"))
    app.add_handler(CallbackQueryHandler(start_menu_cb, pattern="^startmenu$"))

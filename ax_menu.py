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
            "/couple: couple of the day, $500 each\n/q: reply to make a quote sticker\n/qcolors: pick quote colours\n"
            "/td: Truth or Dare lobby\n/truth: random truth\n/dare: random dare\n"
            "/pact: reply to swear a Blood Pact\n/betray: break it, costs $50,000\n/bounty: reply + amount"),
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

PAGES["shop"] = ("🛍", "Title Shop",
    "Spend cash or gems on titles. Your equipped title shows on your /profile card and next to your name in /rankings.\n\n"
    "<b>/shop</b>\n"
    "Opens the shop with every title and its price. Owned titles show a tick. Tap a button to buy. "
    "You need to have used /start once so you have a wallet.\n\n"
    "<b>/titles</b>\n"
    "Shows the titles you own. Tap one to wear it, tap Remove title to wear none. You can switch any time for free.\n\n"
    "<b>Cash titles</b>\n"
    "🌱 Fresh Face $20,000\n"
    "🎀 Sweetheart $50,000\n"
    "🎲 Lucky Charm $125,000\n"
    "🥷 Shadow $250,000\n"
    "💸 Money Maker $500,000\n"
    "🔪 Menace $750,000\n"
    "👑 Royal $1,000,000\n\n"
    "<b>Gem titles</b>\n"
    "💎 Gem Lord 150 gems\n"
    "🌙 Moonlit Legend 400 gems\n"
    "🦋 Rumi's Chosen 1,000 gems\n\n"
    "<b>Example</b>\n"
    "Type /shop, tap Royal, then type /titles and tap Royal to wear it. "
    "Now /profile shows Royal under your level.\n\n"
    "<b>What members see</b>\n"
    "Everyone sees your title beside your name in /rankings. "
    "On the profile card it is shown as plain gold text, in the rankings it also has its emoji.\n\n"
    "<b>Good to know</b>\n"
    "Titles are yours forever, buying never lowers your XP or level, "
    "and the buttons only work for the person who opened the menu.")

PAGES["channel"] = ("\U0001F6E1", "Channel Protection",
    "Add Rumi to your channel as admin and it keeps an eye on it for you. "
    "It never spams your channel.\n\n"
    "<b>How to start</b>\n"
    "1. Tap Add to your channel on /start.\n"
    "2. Pick your channel and keep the suggested admin rights.\n"
    "3. Rumi posts one short Protected by Rumi message with two buttons.\n\n"
    "<b>What the channel sees</b>\n"
    "One post when Rumi joins, then one short post every 2 days. Each deletes itself after 3.5 hours, so your feed stays clean. "
    "Nothing else is ever posted there.\n\n"
    "<b>What you get privately</b>\n"
    "Every few hours Rumi checks your member count. If it drops a lot (10 members or 10 percent, "
    "whichever is bigger), Rumi sends you one private message with the old and new count. "
    "Every week you also get a short summary with your member count and the change. You must have pressed /start with Rumi once so it can message you.\n\n"
    "<b>Example</b>\n"
    "Your channel goes from 500 to 430 members overnight. You get a private message: "
    "went from 500 to 430, minus 70.\n\n"
    "<b>What Rumi cannot do</b>\n"
    "Telegram gives bots no control over reports, so Rumi does not claim to block them. "
    "It helps you notice trouble early.\n\n"
    "<b>Good to know</b>\n"
    "Removing Rumi from the channel stops all checks. Your data for that channel is deleted.")

PAGES["more"] = ("\u2728", "More Commands",
    "/clan: build or join a Clan\n"
    "/topgroups: see the top groups\n"
    "/tagall: mention everyone (admins)\n"
    "/admins: call the group admins\n"
    "/speak: Rumi speaks\n"
    "/kang: take a sticker for yourself\n/mypacks: see your sticker packs\n"
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
    try:
        await q.edit_message_text(text, parse_mode=H, reply_markup=kb)
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        await q.message.reply_text(text, parse_mode=H, reply_markup=kb)


def register(app):
    app.add_handler(CommandHandler("features", features_cmd))
    app.add_handler(CallbackQueryHandler(menu_cb, pattern="^axh:"))
    app.add_handler(CallbackQueryHandler(start_menu_cb, pattern="^startmenu$"))

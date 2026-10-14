"""Moderation help: /help, /modhelp, and the Moderation sub-menu in /features."""
from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler

import ax_menu
from ax_common import btn

H = ParseMode.HTML
PAGES = {
    "bans": ("🔨", "Bans, Mutes, Kicks",
             "Admins only. Reply to someone, or use @username / user ID.\n\n"
             "/ban /unban\n/mute /unmute\n/kick\n"
             "/tban 2h and /tmute 30m: timed (s, m, h, d, w)\n"
             "/dban /dmute /dkick: also deletes their message\n"
             "/sban /smute /skick: silent, your command disappears\n"
             "/kickme: leave the group yourself\n\n"
             "Example: /tmute @user 2h spamming\n"
             "Most actions come with an undo button."),
    "warns": ("⚠️", "Warnings",
              "/warn reason: add a warning\n"
              "/dwarn: warn and delete their message\n"
              "/swarn: silent warn\n"
              "/warns: see someone's warnings\n"
              "/rmwarn: remove the latest one\n"
              "/resetwarn: clear one person\n/resetallwarns: clear everyone\n\n"
              "Settings (per group):\n"
              "/warnlimit 5\n"
              "/warnmode ban, mute, kick, tban or tmute (example: /warnmode tmute 6h)\n"
              "/warntime 4w (or off): warnings expire\n"
              "/warnings: see your settings"),
    "purge": ("🧹", "Purges",
              "/purge: reply to the first message, I delete everything down to now\n"
              "/purge 20: reply to a message, I delete 20 from there\n"
              "/spurge: same, no confirmation message\n"
              "/del: reply to one message to remove it\n"
              "/purgefrom then /purgeto: mark the start, then reply to the end\n\n"
              "I need the Delete messages permission. Telegram only lets me remove messages from the last 48 hours."),
    "admin": ("👮", "Admin Tools",
              "/promote and /demote: reply or @username\n"
              "/adminlist: who runs this group\n"
              "/admincache: refresh the admin list\n"
              "/id: your ID and the group ID, or a replied user's ID"),
}


def mod_home():
    text = "🛡️ <b>Moderation</b>\n<blockquote>Pick a topic to see how each command works 💫</blockquote>"
    b = [btn(f"{i} {t}", f"axhm:{k}", style="primary") for k, (i, t, _) in PAGES.items()]
    rows = [b[j:j + 2] for j in range(0, len(b), 2)]
    rows.append([btn("⬅ Back", "axh:home"), btn("✖ Close", "axh:close", style="danger")])
    return text, InlineKeyboardMarkup(rows)


def mod_page(key):
    i, t, body = PAGES[key]
    kb = InlineKeyboardMarkup([[btn("⬅ Back", "axhm:home"), btn("✖ Close", "axh:close", style="danger")]])
    return f"{i} <b>{t}</b>\n<blockquote>{body}</blockquote>", kb


async def _show(q, text, kb):
    try:
        await q.edit_message_text(text, parse_mode=H, reply_markup=kb)
    except Exception:
        pass


async def mod_open_cb(update, context):
    q = update.callback_query
    await q.answer()
    text, kb = mod_home()
    await _show(q, text, kb)
    raise ApplicationHandlerStop  # stop ax_menu's older Moderation page from also running


async def sub_cb(update, context):
    q = update.callback_query
    await q.answer()
    key = q.data.split(":")[1]
    text, kb = mod_home() if key == "home" or key not in PAGES else mod_page(key)
    await _show(q, text, kb)


async def help_cmd(update, context):
    text, kb = ax_menu.home()
    await update.effective_message.reply_text(text, parse_mode=H, reply_markup=kb)


async def modhelp_cmd(update, context):
    text, kb = mod_home()
    await update.effective_message.reply_text(text, parse_mode=H, reply_markup=kb)


def register(app):
    app.add_handler(CallbackQueryHandler(mod_open_cb, pattern="^axh:mod$"), group=-1)
    app.add_handler(CallbackQueryHandler(sub_cb, pattern="^axhm:"))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("modhelp", modhelp_cmd))

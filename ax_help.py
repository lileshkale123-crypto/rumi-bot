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
    "approve": ("✅", "Approvals",
                "Approved users skip locks, blocklists and flood checks.\n\n"
                "/approve: reply or @username\n/unapprove\n/approved: list everyone\n"
                "/unapproveall\n/approval: check someone's status"),
    "locks": ("🔒", "Locks",
              "Locked things get deleted for normal members. Admins and approved users are exempt.\n\n"
              "/lock sticker photo: lock one or more types\n/unlock sticker\n/locks: see what is locked\n"
              "/locktypes: every lock type\n/lockwarns on: breaking a lock adds a warning\n\n"
              "Custom punishment: /lock invitelink ### no ads {ban}\n"
              "Reset it: /lock invitelink ###\n\n"
              "Allowlist: /allowlist @channel or a link, /rmallowlist, /rmallowlistall"),
    "blocklist": ("🚫", "Blocklists",
                  "Messages with blocklisted words get deleted.\n\n"
                  "/addblocklist word reason\n/addblocklist \"a sentence\" reason\n"
                  "/addblocklist (hi, hey) reason: several at once\n/rmblocklist word\n/blocklist\n\n"
                  "Wildcards: ? one character, * a run of characters, ** anything\n"
                  "Prefixes: exact:hi and prefix:hi\n\n"
                  "Settings: /blocklistmode warn (or ban, mute, kick, tban, tmute)\n"
                  "/blocklistdelete on or off\n/setblocklistreason text\n\n"
                  "Per word: /addblocklist boo Scary {tmute 6h} or {warn} {nodel}"),
    "flood": ("🌊", "Antiflood",
              "Stops people sending too many messages too fast.\n\n"
              "/flood: see your settings\n/setflood 7: trigger after 7 messages\n"
              "/setfloodtimer 10 30s: 10 messages within 30 seconds\n/setfloodtimer off\n"
              "/floodmode ban, mute, kick, tban or tmute (example: /floodmode tmute 3h)\n"
              "/clearflood on: also delete the flood messages\n\n"
              "Admins and approved users are never affected."),
    "raid": ("🚨", "Antiraid",
             "During a raid, every new member is temp-banned.\n\n"
             "/antiraid: switch on (or /antiraid 3h for a set time)\n/antiraid off\n"
             "/raidtime 6h: how long raid mode lasts\n/raidactiontime 1h: how long joiners are banned\n"
             "/autoantiraid 15: starts by itself if 15 people join in a minute\n/autoantiraid off"),
    "rules": ("📜", "Rules",
              "/rules: read the group rules\n/setrules text: set them (admins)\n/resetrules\n"
              "/privaterules on: /rules is sent to their PM\n"
              "/setrulesbutton text and /resetrulesbutton: name of the rules button"),
    "report": ("🚨", "Reports",
               "Members can call the admins to a bad message.\n\n"
               "Reply to the message with /report or @admin\n"
               "/reports on or off (admins)\n\n"
               "Admins cannot report, and admins cannot be reported."),
    "pins": ("📌", "Pins",
             "/pin: reply to a message (add loud to notify everyone)\n"
             "/permapin text: I send and pin your text\n/unpin: reply to unpin one, or unpins the latest\n"
             "/unpinall\n/pinned: jump to the pinned message\n"
             "/antichannelpin on: undo Telegram's auto-pin of channel posts\n"
             "/cleanlinked on: delete posts from the linked channel"),
    "welcome": ("👋", "Welcome",
               "/welcome on or off: greet new members\n"
               "/setwelcome text: set the message (or send a photo with it)\n"
               "/resetwelcome: back to default\n"
               "/goodbye, /setgoodbye, /resetgoodbye: same for leavers\n"
               "/cleanwelcome on: delete the old welcome\n"
               "/welcomehelp: all fillings, random replies and buttons"),
    "captcha": ("🔐", "Captcha",
               "<b>What it does</b>\n"
               "New members are muted the moment they join. They must "
               "prove they are human before they can chat. Bots and "
               "spammers usually fail, so they never reach your members.\n\n"
               "<b>Turn it on</b>\n"
               "/welcome on first, then /captcha on. /captcha alone shows "
               "your current settings.\n\n"
               "<b>Modes</b> (/captchamode)\n"
               "button: tap one button. Easiest for members.\n"
               "math: answer a sum like 4 + 7 = ?\n"
               "text: type a short code shown in the message.\n\n"
               "<b>Time limits</b>\n"
               "/captchakicktime 5m: how long they have to verify.\n"
               "/captchakick on: remove people who do not verify "
               "(they can join again later).\n"
               "/captchamutetime 1h: keep them muted for a while after "
               "verifying. 0 means no extra mute.\n"
               "Times use s, m, h, d, w (example 30m, 2h).\n\n"
               "<b>Custom button text</b>\n"
               "/setcaptchatext I am real: changes the button label.\n"
               "/resetcaptchatext: back to the default.\n\n"
               "Admins and approved members skip the captcha."),
    "clean": ("🧹", "Cleaning",
               "<b>What it does</b>\n"
               "Keeps your group tidy by deleting messages the bot does "
               "not need you to see.\n\n"
               "<b>Clean commands</b>\n"
               "Deletes the command message itself a few seconds after "
               "it runs, so /warn or /ban do not fill the chat.\n"
               "/cleancommand all: clean every type\n"
               "/cleancommand admin: only commands sent by admins\n"
               "/cleancommand user: only commands sent by members\n"
               "/cleancommand other: commands aimed at other bots\n"
               "/keepcommand user: stop cleaning that type\n"
               "/cleancommand alone shows what is on.\n\n"
               "<b>Clean service messages</b>\n"
               "Service messages are the grey lines Telegram adds, like "
               "Asha joined or Raj pinned a message.\n"
               "/cleanservice join: delete joined lines\n"
               "/cleanservice leave: delete left lines\n"
               "Other types: photo, pin, title, videochat, other, all.\n"
               "/keepservice or /nocleanservice: stop cleaning a type\n"
               "/cleanservicetypes: what each type means\n\n"
               "Example: /cleanservice join leave"),
    "disable": ("🚫", "Disabling",
               "<b>What it does</b>\n"
               "Stops members from using a command in your group, "
               "without removing the bot feature for other groups.\n\n"
               "/disable rob: members can no longer use /rob here\n"
               "/enable rob: allow it again\n"
               "/disabled: list what is disabled\n"
               "/disableable: commands that can never be disabled\n\n"
               "<b>Options</b>\n"
               "/disabledel on: also delete the blocked command message\n"
               "/disableadmin on: block admins too (off by default)\n\n"
               "Example: /disable spin"),
    "logs": ("📋", "Log Channel",
               "<b>What it does</b>\n"
               "Sends a record of every ban, mute, kick and warn to a "
               "private channel, so your team can review them.\n\n"
               "<b>Set up</b>\n"
               "1. Create a channel and add Rumi as admin.\n"
               "2. In your group send /setlog -100123456789 (the "
               "channel id).\n"
               "3. Rumi posts a test line to confirm.\n\n"
               "/logchannel: show the link and what is off\n"
               "/unsetlog: remove the link\n"
               "/nolog warn: stop logging one category\n"
               "/log warn: log it again\n"
               "/logcategories: ban, mute, kick, warn, lock, blocklist, "
               "flood, settings"),
    "misc3": ("⚙️", "Admin Settings",
               "<b>/anonadmin on</b>\n"
               "Admins who post as the group (anonymous) can use admin "
               "commands. Off by default for safety.\n\n"
               "<b>/silentactions on</b>\n"
               "Hides ban, mute and warn notices from the group. Admins "
               "still see them in the log channel, so link one with "
               "/setlog first.\n\n"
               "<b>/adminerror on or off</b>\n"
               "Whether members get a reply when they try an admin "
               "command. (Setting is saved; full effect comes in a later update.)"),
    "notes": ('📝', 'Notes', '<b>What it does</b>\n<blockquote>Notes are saved messages your group can read any time. Great for rules, links, FAQs and group info. Admins save them, everyone can read them.</blockquote>\n\n<b>Commands</b>\n/save name text: save a note. You can also reply to any message (text, photo, video, sticker, voice, file) with /save name and Rumi keeps it.\n/get name: show a note. Add noformat to see the raw text with all the codes, like /get rules noformat\n#name: typing #rules in the chat works exactly like /get rules\n/notes: list every note in this group\n/clear name: delete one note\n/clearallnotes: delete all notes\n/privatenotes on or off: when on, a note shows an Open note button and members read it in a private chat with Rumi, so the group stays clean\n\n<b>Example</b>\n<blockquote>/save rules Hi {first}, be kind and no spam</blockquote>\nMembers type #rules and see: Hi Aarav, be kind and no spam\n\n<b>Fillings</b> (replaced for each reader)\n{first} first name, {last} last name, {fullname} both, {username} username, {mention} a tap-to-open name, {id} user id, {chatname} group name\n\n<b>Random replies</b>\nPut %%% between answers and Rumi picks one each time.\nExample: /save hello Hey there %%% Hi hi %%% Welcome in\n\n<b>Buttons</b>\nAdd a link button with [Text](buttonurl://https://example.com)\nAdd :same to put it on the same row as the one before: [Text](buttonurl://https://example.com:same)\nColours: add :blue, :green or :red\nA button can also open another note: [Rules](buttonurl://#rules)\n\n<b>Special tags</b> (put them anywhere in the note)\n{private}: this note always opens in private\n{noprivate}: this note always shows in the group\n{admin}: only admins can read this note\n{nonotif}: send without a sound\n{protect}: stop forwarding and saving\n{mediaspoiler}: hide photos and videos behind a spoiler'),
    "filters": ('🔎', 'Filters', '<b>What it does</b>\n<blockquote>A filter is an automatic reply. When someone writes a trigger word, Rumi answers on its own. Great for greetings, FAQs and common questions.</blockquote>\n\n<b>Commands</b>\n/filter word reply: set an automatic reply. You can also reply to a photo, video, sticker or file with /filter word\n/filters: list every filter in this group\n/stop word: remove one filter\n/clearfilters: remove all filters\n\n<b>Example</b>\n<blockquote>/filter price Our plans start at 99 rupees</blockquote>\nNow when anyone writes the word price anywhere in a message, Rumi sends that reply.\n\n<b>Trigger options</b> (put these in front of the word)\nexact:word : Rumi replies only when the whole message is exactly that word.\nExample: /filter exact:hi Hello there\nprefix:word : Rumi replies when the message starts with that word.\nExample: /filter prefix:good Have a great day\nWith no option, Rumi replies when the word appears anywhere as a whole word.\n\n<b>Fillings</b> (replaced for each person)\n{first} first name, {last} last name, {fullname} both, {username} username, {mention} a tap-to-open name, {id} user id, {chatname} group name\nExample: /filter hello Hey {first}, welcome to {chatname}\n\n<b>Random replies</b>\nPut %%% between answers and Rumi picks one each time.\nExample: /filter gm Good morning %%% Gm gm %%% Rise and shine\n\n<b>Buttons</b>\nAdd a link button with [Text](buttonurl://https://example.com)\nAdd :same to share a row, and :blue, :green or :red for colours.\nA button can also open a saved note: [Rules](buttonurl://#rules)\n\n<b>Other tags</b>\n{nonotif}: send without a sound\n{protect}: stop forwarding and saving\n{mediaspoiler}: hide photos and videos behind a spoiler\n\n<b>What members see</b>\nMembers just write a message. Rumi replies to it with your saved answer. They never need a command.'),
    "backup": ("📦", "Backup & Reset", """<b>What it does</b>
<blockquote>Saves all of this group's Rumi settings into one small file, so you can restore them later or copy them to another group. Perfect when you set up a group carefully and never want to redo it 💫</blockquote>

<b>What is saved</b>
Notes, filters, rules, locks, blocklist, allowlist, welcome and goodbye texts, captcha, antiflood, antiraid, report, pin, cleaning, disabled commands, log channel, warn settings and other group settings.

<b>What is never touched</b>
Wallets, coins, ranks, warns of members, approved members, AFK and anything personal. Backups only hold group settings.

<b>Commands</b>
/export: sends you the backup file. Any admin can use it.
/import: loads a backup file into this group. Only the group creator can use it.
/reset: clears all Rumi settings of this group. Only the group creator can use it, and it asks to confirm first.

<b>How to export</b>
Type /export in your group. Rumi sends a file named like rumi_backup_12345.json. Keep it safe, it is your backup.

<b>How to import</b>
1. Send the backup file into the group you want to fill.
2. Reply to that file with /import
3. Rumi loads everything and tells you how many entries were added.
Important: import replaces the settings of the sections found in the file. Anything in those sections that is not in the file is removed. If something goes wrong, nothing is changed.

<b>How to reset</b>
Type /reset. Rumi shows two buttons: Yes, reset and Cancel. Only the group creator can press them. After Yes, every setting listed above is wiped and the group starts fresh.

<b>Example</b>
<blockquote>Group A is perfect. In group A: /export
Open group B, forward the file there, reply to it with /import
Group B now has the same notes, filters and rules ✨</blockquote>

<b>What members see</b>
Members see nothing special. Only admins and the creator use these commands. If a normal member tries, Rumi politely says it is for admins or the creator only.

<b>Good to know</b>
Active raid timers and the last welcome message are not saved on purpose, so an old raid mode never comes back by mistake.
"""),
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

"""Extra pages for the /start Features menu (and /features): Powers, Kill, Gifts, Friends, Reactions, Recovery.

Edits ax_menu.PAGES in place when imported, so ax_menu.py itself stays untouched.
To change a page, edit its text below. Order of the buttons = ORDER at the bottom.
"""
import ax_menu

NEW: dict[str, tuple[str, str, str]] = {}

NEW["chat"] = ("💬", "Chat and Economy",
    "Say rumi or reply to my message and I'll chat with you.\n"
    "I also react to messages with emojis.\n\n"
    "/start: your snapshot and games\n"
    "/wallet /claim /pay /guard /rob /rankings\n"
    "/vault /deposit /withdraw\n"
    "/convert: swap coins and gems (10,000 coins = 1 gem, up to 20 gems per season)\n"
    "/toprich: top 10 richest, global and in this group\n"
    "/toplegends: the legends of the last season\n"
    "/bluff /mafia /stopmafia /mafiatop\n\n"
    "Want to play? Tap Games on /start.")

NEW["powers"] = ("🐺", "Premium Powers",
    "Premium members can switch on special powers for a number of days. Each power costs gems per day.\n\n"
    "🐺 <b>Shadow Heist</b> (heist): 5 💎 a day\n"
    "Rob up to $100,000 in one /rob (normally $30,000 for Premium) and get 100 extra /rob tries a day.\n\n"
    "⚔️ <b>Crimson Reaper</b> (reaper): 4 💎 a day\n"
    "+$150 on every /kill and 100 extra /kill tries a day.\n\n"
    "🛡️ <b>Iron Fortress</b> (fortress): 4 💎 a day\n"
    "Your wallet is kept safe from robbers at 50% instead of 30%.\n\n"
    "<b>Commands</b>\n"
    "/powers: see all powers and prices\n"
    "/pinfo heist: details of one power\n"
    "/act heist 3: switch a power on for 3 days (up to 30)\n"
    "/mypowers or /mp: your active powers and the time left\n"
    "/ph: quick help\n\n"
    "<b>Good to know</b>\n"
    "Powers are for Premium members only (see /premium). Using a power you already have adds days to it. "
    "You can spend up to 50 💎 a day on powers.")

NEW["kill"] = ("⚔️", "Kill and Revive",
    "<b>/kill</b>: reply to someone to knock them out.\n"
    "You earn $100 to $200 and up to 10 XP. Premium earns $200 to $400 and 10 to 20 XP.\n"
    "The person you killed is dead for 7 to 8 hours, and a dead player can't use /kill.\n"
    "Daily limit: 200 kills (Premium 400, Crimson Reaper adds 100 more). It resets at midnight IST.\n\n"
    "<b>/revive</b>: bring yourself back, or reply to someone to bring them back.\n"
    "It costs $300 to $400, paid by whoever types /revive.\n\n"
    "<b>Example</b>\n"
    "Reply to a friend with /kill, then reply to them with /revive to save them.")

NEW["gifts"] = ("🎁", "Gifts",
    "Buy gifts and send them to friends.\n\n"
    "<b>/giftshop</b>: open the shop and tap a gift to buy it\n"
    "<b>/sendgift rose</b>: reply to someone to send them a gift. If you don't own one, it is bought for you.\n"
    "<b>/items</b>: your gifts, and the ones you received (reply to see someone else's)\n\n"
    "<b>Prices</b>\n"
    "🌹 Rose $1,500 • 🌷 Tulip $2,500 • 💌 Love Letter $4,000\n"
    "🎁 Surprise Box $5,000 (opens to a random gift) • 💐 Bouquet $6,000\n"
    "👨 Boyfriend $10,000 • 👩 Girlfriend $15,000 • 👑 Crown $22,000\n"
    "💎 Diamond $40,000 • 🏎 Lamborghini $60,000 • 🚗 Ferrari $75,000\n"
    "🛥 Yacht $150,000 • 🏰 Mansion $300,000 • ✈️ Private Jet $500,000")

NEW["friends"] = ("👥", "Friends and Tags",
    "Save your friends under short tags, then call them together in one message.\n\n"
    "/addf gang: reply to someone to save them as \"gang\" (or /addf @user gang)\n"
    "/tag gang or /t gang: mention everyone saved under that tag\n"
    "/allf: list the friends you saved\n"
    "/friend: how many friends you have\n\n"
    "<b>Mention everyone</b>\n"
    "/all or @all: calls the group in small batches (one use every 5 minutes per group)\n\n"
    "<b>Your intro</b>\n"
    "/setintro text: write a short bio (400 characters)\n"
    "/intro: show your intro, or reply to see someone else's\n\n"
    "<b>Name history</b>\n"
    "/detail: reply to someone to see their ID and their last 4 names and usernames")

NEW["react"] = ("🎭", "Reactions and Meters",
    "Reply to someone with a command. All free.\n\n"
    "<b>Reactions</b> (with a GIF)\n"
    "/hug /kiss /pat /slap /punch /bite /poke /wink /cuddle /bonk /murder\n\n"
    "<b>Meters</b> (the result is the same for the same pair all day)\n"
    "/love: how much you two match\n"
    "/crush: how big your crush is\n"
    "/look: how good they look\n"
    "/brain: how smart they are\n"
    "/stupid_meter: how silly they are\n\n"
    "<b>Couples</b>\n"
    "/couples: today's random couple of the group\n"
    "/ship, /match and /couple are on the Fun and Social page.")

NEW["safety"] = ("🔐", "Account Recovery",
    "Lost your Telegram account? Set a recovery password now so your coins, gems and Premium can be moved to a new account later.\n\n"
    "/setpass password: set it (4+ characters). Do it in DM, in a group your message is deleted right after.\n"
    "/mpass: see if your password is set (DM)\n"
    "/cpass old new: change your password\n"
    "/merge old_user_id password: on your new account, take over a deleted account's coins, vault cash, gems and Premium\n\n"
    "<b>Good to know</b>\n"
    "/merge only works if the old Telegram account is really deleted and had a password. "
    "XP, level and stats stay separate.")

NEW["events"] = ("🎁", "Events and Daily Tasks",
    "<b>Group events</b>\n"
    "Once a day, in groups with 25+ members, an event can pop up while people are chatting.\n"
    "🎴 <b>/catch name</b>: a character appears, be the first to type its name and win $2,000.\n"
    "🎯 <b>/g 250</b>: guess the secret number (100 to 500). I tell you higher or lower. Win $500 to $1,000. The group shares 12 tries.\n\n"
    "<b>Daily tasks</b> (/task)\n"
    "Two tasks a day. Finish both to win $9,000.\n"
    "1. Word: your letters have one wrong letter. Use /word to see them and reply to a friend with /exch WANT GIVE to swap.\n"
    "2. Action: do 20 to 25 /kill, 20 to 25 /rob, or win 2 games.\n"
    "It resets at midnight IST.")

ORDER = [
    "set", "notes", "chat", "events", "powers", "kill", "gifts", "shop", "fun", "react",
    "friends", "you", "safety", "invite", "premium", "channel", "more", "broadcast",
]


def _apply() -> None:
    old = dict(ax_menu.PAGES)
    merged = {**old, **NEW}

    # Premium page: add /check once
    i, t, body = merged["premium"]
    if "/check" not in body:
        extra = ("\n/check: Premium only. Reply to someone in a group and their protection time is sent to your DM.\n"
                 "/powers: Premium powers (see the Premium Powers page)\n")
        marker = "Owner only:"
        body = body.replace(marker, extra + "\n" + marker, 1) if marker in body else body + extra
        merged["premium"] = (i, t, body)

    # Invite page: add the leaderboard once
    i, t, body = merged["invite"]
    if "/topinvite" not in body:
        merged["invite"] = (i, t, body.replace(
            "/referral: see your link and how many friends you have brought.\n",
            "/referral or /invite: see your link and how many friends you have brought.\n"
            "/topinvite: the top 10 people who invited the most friends.\n", 1))

    # More page: add /admin once
    i, t, body = merged["more"]
    if "/admin" not in body.replace("/admins", ""):
        merged["more"] = (i, t, body.replace("/admins: call the group admins\n",
                                             "/admins: call the group admins\n/admin: show the admin list\n", 1))

    ax_menu.PAGES.clear()
    for key in ORDER:
        if key in merged:
            ax_menu.PAGES[key] = merged[key]
    for key, val in merged.items():  # anything not in ORDER still shows up, at the end
        ax_menu.PAGES.setdefault(key, val)


_apply()


def register(dp) -> None:
    """Nothing to register: the pages are added when this module is imported."""
    return None

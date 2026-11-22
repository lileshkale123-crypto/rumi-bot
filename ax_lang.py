"""Language choice: /setlang (personal in DM, group default in groups) & /setgrouplang (aiogram 3.x, bot_id aware)."""
import logging

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import ax_i18n
import bot_context
from ax_common import esc, is_admin
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="ax_lang")
H = ParseMode.HTML

LANGS: dict[str, str] = {
    "en": "🇬🇧 English",
    "hi": "🇮🇳 हिन्दी (शुद्ध हिंदी)",
}


def _txt(code: str, bot_id: int | None = None) -> dict[str, str]:
    brand = get_brand(bot_id)
    bname = esc(brand.name)
    if code == "hi":
        hi_name = "रूमी" if brand.name.lower() == "rumi" else bname
        return {
            "pick_dm": (
                "🌐 <b>अपनी भाषा चुनें</b>\n"
                f"<blockquote>%s, चुनें कि निजी बातचीत में {hi_name} आपसे किस भाषा में संवाद करे 💫</blockquote>"
            ),
            "done_dm": (
                "✅ <b>भाषा सहेजी गई</b>\n"
                f"<blockquote>%s, अब {hi_name} आपसे शुद्ध हिंदी में संवाद करेगी ✨</blockquote>"
            ),
            "pick_grp": (
                "🌐 <b>समूह की भाषा चुनें</b>\n"
                "<blockquote><b>%s</b> के लिए मूल भाषा चुनें।\n"
                "इस समूह के सभी संदेश, बटन, सूचनाएँ, खेल और सत्य-चुनौती प्रश्न इसी भाषा में दिखाई देंगे 💫</blockquote>"
            ),
            "done_grp": (
                "✅ <b>समूह की भाषा सहेजी गई</b>\n"
                "<blockquote><b>%s</b> की भाषा अब शुद्ध हिंदी तय कर दी गई है ✨</blockquote>"
            ),
            "admin_only": (
                "🛡️ <b>केवल प्रशासकों के लिए</b>\n"
                "<blockquote>केवल समूह प्रशासक ही समूह की मूल भाषा बदल सकते हैं।\n"
                "वर्तमान समूह भाषा: <b>%s</b>\n"
                "संकेत: अपनी व्यक्तिगत भाषा बदलने के लिए मुझे निजी संदेश में /setlang भेजें ✨</blockquote>"
            ),
            "admin_alert": "केवल समूह प्रशासक ही समूह की भाषा बदल सकते हैं 🛡️",
        }
    return {
        "pick_dm": (
            "🌐 <b>Choose your language</b>\n"
            f"<blockquote>%s, pick the language {bname} will use with you in private chat 💫</blockquote>"
        ),
        "done_dm": (
            "✅ <b>Language saved</b>\n"
            f"<blockquote>%s, {bname} will now speak with you in English ✨</blockquote>"
        ),
        "pick_grp": (
            "🌐 <b>Choose group language</b>\n"
            "<blockquote>Select the default language for <b>%s</b>.\n"
            "All messages, buttons, alerts, games and Truth or Dare prompts in this group will use it 💫</blockquote>"
        ),
        "done_grp": (
            "✅ <b>Group language saved</b>\n"
            "<blockquote><b>%s</b> is now set to English ✨</blockquote>"
        ),
        "admin_only": (
            "🛡️ <b>Admins only</b>\n"
            "<blockquote>Only group admins can change the group default language.\n"
            "Current group language: <b>%s</b>\n"
            "Tip: send /setlang in my private chat to set your personal language ✨</blockquote>"
        ),
        "admin_alert": "Only group admins can change the group language 🛡️",
    }


# One list drives the menu: the order here is the order users see after typing "/".
# Moderation and owner commands are left out on purpose (they are in /modhelp and /settings).
_MENU = [
    # --- Economy ---
    ("wallet", "Check your cash, gems & level"),
    ("claim", "Claim your daily reward"),
    ("work", "Complete a contract for coins"),
    ("spin", "Spin the daily Wheel of Fate"),
    ("pay", "Send coins to someone (reply)"),
    ("rob", "Attempt a heist on someone (reply)"),
    ("guard", "Raise a protective guard shield"),
    ("vault", "Open your secure coin vault"),
    ("deposit", "Put coins into your vault"),
    ("withdraw", "Take coins out of your vault"),
    ("rankings", "View top richest, XP & gems"),
    ("toprich", "Richest players leaderboard"),
    ("topgroups", "Top groups leaderboard"),
    ("bounty", "Put a bounty on someone (reply)"),
    ("pact", "Offer someone a Blood Pact (reply)"),
    ("betray", "Break your Blood Pact"),
    # --- Games & fun ---
    ("kill", "Attack someone (reply)"),
    ("revive", "Come back after being killed"),
    ("bluff", "Play Shadow Bluff in groups"),
    ("mafia", "Start a Mafia game in groups"),
    ("stopmafia", "Stop the running Mafia game"),
    ("mafiatop", "Mafia leaderboard"),
    ("td", "Play Truth or Dare"),
    ("truth", "Get a random truth question"),
    ("dare", "Get a random dare"),
    ("ship", "Ship two people together"),
    ("match", "Find your match in the group"),
    ("couple", "Couple of the day"),
    ("couples", "Today's couples in the group"),
    ("love", "Love meter"),
    ("crush", "Crush meter"),
    ("look", "Look meter"),
    ("brain", "Brain meter"),
    ("stupid_meter", "Stupid meter"),
    ("hug", "Give someone a hug (reply)"),
    ("kiss", "Give someone a kiss (reply)"),
    ("pat", "Pat someone (reply)"),
    ("slap", "Slap someone (reply)"),
    ("punch", "Punch someone (reply)"),
    ("bite", "Bite someone (reply)"),
    ("poke", "Poke someone (reply)"),
    ("wink", "Wink at someone (reply)"),
    ("cuddle", "Cuddle someone (reply)"),
    ("bonk", "Bonk someone (reply)"),
    ("murder", "Dramatic murder (reply)"),
    # --- Profile, shop & community ---
    ("profile", "View your visual profile card"),
    ("shop", "Buy custom titles"),
    ("titles", "Your titles"),
    ("giftshop", "Buy & send animated gifts"),
    ("sendgift", "Send a gift to someone (reply)"),
    ("mygifts", "See your gifts"),
    ("clan", "Create or manage your clan"),
    ("referral", "Invite friends and earn rewards"),
    ("premium", "Get {b} Premium"),
    ("donate", "Support {b}"),
    # --- Tools ---
    ("q", "Turn a reply into a quote sticker"),
    ("qcolors", "Quote sticker colour names"),
    ("mypacks", "Your sticker packs"),
    ("kang", "Add a sticker or photo to your pack"),
    ("speak", "Convert text to voice"),
    ("afk", "Mark yourself away"),
    ("info", "Info about a user"),
    ("id", "Show user and chat IDs"),
    ("report", "Report a message to the admins (reply)"),
    ("rules", "Show the group rules"),
    ("get", "Get a saved note"),
    ("notes", "List the saved notes"),
    # --- Bot ---
    ("help", "Full guide & features menu"),
    ("features", "Browse all features"),
    ("start", "Open {b} welcome card & snapshot"),
    ("setlang", "Choose language (personal or group)"),
    ("ping", "Check if the bot is fast"),
    ("privacy", "Privacy information"),
]


def _commands_en(brand_name: str) -> list[BotCommand]:
    return [BotCommand(command=c, description=d.replace("{b}", brand_name)) for c, d in _MENU]


def _commands_hi(brand_name: str) -> list[BotCommand]:
    """Hindi text where it exists; the English line for commands that have no Hindi text yet."""
    hindi = {c.command: c.description for c in _commands_hi_old(brand_name)}
    return [
        BotCommand(command=c, description=hindi.get(c) or d.replace("{b}", brand_name))
        for c, d in _MENU
    ]


def _commands_hi_old(brand_name: str) -> list[BotCommand]:
    hi_name = "रूमी" if brand_name.lower() == "rumi" else brand_name
    return [
        BotCommand(command="start", description=f"{hi_name} का मुख्य पृष्ठ और आपका सारांश"),
        BotCommand(command="help", description="संपूर्ण मार्गदर्शिका और विशेषताएँ"),
        BotCommand(command="setlang", description="भाषा चुनें (व्यक्तिगत या समूह)"),
        BotCommand(command="wallet", description="अपना बटुआ, नकद, रत्न और स्तर देखें"),
        BotCommand(command="claim", description="अपना दैनिक इनाम प्राप्त करें"),
        BotCommand(command="pay", description="किसी को सिक्के भेजें (उत्तर देकर)"),
        BotCommand(command="rob", description="किसी पर लूट का प्रयास करें (उत्तर देकर)"),
        BotCommand(command="guard", description="अपना सुरक्षा कवच सक्रिय करें"),
        BotCommand(command="vault", description="अपनी सुरक्षित तिजोरी खोलें"),
        BotCommand(command="rankings", description="सर्वाधिक धनी, अनुभव व रत्न सूची"),
        BotCommand(command="profile", description="अपना आकर्षक प्रोफ़ाइल कार्ड देखें"),
        BotCommand(command="shop", description="विशेष उपाधियाँ खरीदें"),
        BotCommand(command="giftshop", description="उपहार खरीदें और भेजें"),
        BotCommand(command="clan", description="कबीला बनाएँ या प्रबंधित करें"),
        BotCommand(command="bluff", description="समूह में छाया छल खेलें"),
        BotCommand(command="mafia", description="समूह में माफ़िया खेल शुरू करें"),
        BotCommand(command="td", description="सत्य या चुनौती खेलें"),
        BotCommand(command="q", description="संदेश को उद्धरण स्टिकर में बदलें"),
        BotCommand(command="kang", description="स्टिकर या चित्र अपने संग्रह में जोड़ें"),
        BotCommand(command="speak", description="पाठ को वाणी में बदलें"),
        BotCommand(command="settings", description="समूह प्रबंधन और स्वागत व्यवस्थाएँ"),
        BotCommand(command="modhelp", description="संपूर्ण प्रबंधन आदेश मार्गदर्शिका"),
        BotCommand(command="premium", description=f"{hi_name} प्रीमियम प्राप्त करें"),
    ]


async def get_lang(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None) -> str:
    bid = bot_context.resolve_bot_id(bot_id)
    try:
        async with pool.acquire() as c:
            v = await c.fetchval(
                "SELECT lang FROM ax_user_lang WHERE bot_id=$1 AND user_id=$2",
                bid,
                user_id,
            )
    except Exception:
        return "en"
    return v if v in LANGS else "en"


async def get_chat_lang(pool: asyncpg.Pool, chat_id: int, bot_id: int | None = None) -> str:
    bid = bot_context.resolve_bot_id(bot_id)
    try:
        async with pool.acquire() as c:
            v = await c.fetchval(
                "SELECT lang FROM ax_chat_lang WHERE bot_id=$1 AND chat_id=$2",
                bid,
                chat_id,
            )
    except Exception:
        return "en"
    return v if v in LANGS else "en"


def _lang_keyboard(scope: str, current: str) -> InlineKeyboardMarkup:
    buttons = []
    for code, label in LANGS.items():
        mark = "✅ " if code == current else ""
        buttons.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"axlg_{scope}:{code}"))
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _handle_group_setlang(message: Message, bot: Bot, pool: asyncpg.Pool, bot_id: int) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    cur = await get_chat_lang(pool, chat.id, bot_id=bot_id)
    pack = _txt(cur, bot_id)
    if not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await message.reply(
            pack["admin_only"] % esc(LANGS.get(cur, cur)),
            parse_mode=H,
        )
        return
    await message.reply(
        pack["pick_grp"] % esc(chat.title or "this group"),
        parse_mode=H,
        reply_markup=_lang_keyboard("g", cur),
    )


@router.message(Command("setlang"))
async def setlang_cmd(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    if message.chat.type in ("group", "supergroup"):
        await _handle_group_setlang(message, bot, pool, bid)
        return
    cur = await get_lang(pool, user.id, bot_id=bid)
    pack = _txt(cur, bid)
    await message.reply(
        pack["pick_dm"] % esc(user.first_name or "friend"),
        parse_mode=H,
        reply_markup=_lang_keyboard("u", cur),
    )


@router.message(Command("setgrouplang"))
async def setgrouplang_cmd(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await setlang_cmd(message, bot, pool, bot_ctx)
        return
    await _handle_group_setlang(message, bot, pool, bot_ctx.bot_id)


@router.callback_query(F.data.startswith("axlg_"))
async def setlang_cb(
    query: CallbackQuery,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    raw = (query.data or "")[5:]
    if ":" in raw:
        scope, code = raw.split(":", 1)
    else:
        scope, code = "u", raw

    if code not in LANGS:
        await query.answer()
        return

    bid = bot_ctx.bot_id
    if scope == "g" and query.message is not None and query.message.chat.type in ("group", "supergroup"):
        chat = query.message.chat
        cur = await get_chat_lang(pool, chat.id, bot_id=bid)
        if not await is_admin(bot, chat.id, query.from_user.id, "can_change_info"):
            await query.answer(_txt(cur, bid)["admin_alert"], show_alert=True)
            return
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_chat_lang (bot_id, chat_id, lang) VALUES ($1,$2,$3) "
                "ON CONFLICT (chat_id) DO UPDATE SET bot_id=$1, lang=$3",
                bid,
                chat.id,
                code,
            )
        ax_i18n.set_chat_lang(chat.id, code, bot_id=bid)
        await query.answer()
        pack = _txt(code, bid)
        try:
            await query.message.edit_text(
                pack["done_grp"] % esc(chat.title or "this group"),
                parse_mode=H,
            )
        except Exception:
            pass
        return

    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_user_lang (bot_id, user_id, lang) VALUES ($1,$2,$3) "
            "ON CONFLICT (user_id) DO UPDATE SET bot_id=$1, lang=$3",
            bid,
            query.from_user.id,
            code,
        )
    ax_i18n.set_user_lang(query.from_user.id, code, bot_id=bid)
    await query.answer()
    if query.message:
        pack = _txt(code, bid)
        try:
            await query.message.edit_text(
                pack["done_dm"] % esc(query.from_user.first_name or "friend"),
                parse_mode=H,
            )
        except Exception:
            pass


async def on_startup(bot: Bot, dispatcher) -> None:
    """Register localized Telegram `/` command menu descriptions for English & Pure Hindi."""
    brand = get_brand(bot.id)
    try:
        await bot.set_my_commands(_commands_en(brand.name))
        await bot.set_my_commands(_commands_hi(brand.name), language_code="hi")
        log.info("Registered localized bot command menus (en, hi)")
    except Exception as exc:
        log.warning("set_my_commands failed: %s", exc)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

# --- command menu patch applied ---

"""Six chat-active multiplayer economy games: /dice, /bomb, /flip, /card, /roulette, /hack + Winner Photo & Auto-Pin."""
import asyncio, logging, random, uuid
import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
import ax_common, config, services
from ax_adminpanel import OWNER_IDS
from ax_common import btn, cmd_args, esc
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_arcade")
H = ParseMode.HTML
_GAMES: dict[tuple[int, int], dict] = {}
_CLOSED: set[tuple[int, int]] = set()
GAMES = {
    "dice": ("🎲", "Dice Duel", "/dice 500"),
    "bomb": ("💣", "Bomb Pass", "/bomb 500"),
    "flip": ("🪙", "Coin Flip Clash", "/flip 500"),
    "card": ("🃏", "21 Card Rush", "/card 500"),
    "roulette": ("🎡", "Syndicate Roulette", "/roulette 500"),
    "hack": ("⚡", "Vault Wire Hack", "/hack 500"),
}

async def announce_and_pin_winner(bot: Bot, chat_id: int, winner_id: int, winner_name: str, game_title: str, net_gain: int, tax: int) -> None:
    caption = f"👑 <b>{esc(game_title.upper())} WINNER!</b>\n<blockquote>🏆 <b>{esc(winner_name)}</b>\n💰 Won: <code>+${net_gain:,}</code> (Tax: <code>${tax:,}</code>)</blockquote>"
    sent = None
    try:
        photos = await bot.get_user_profile_photos(winner_id, limit=1)
        if photos.total_count > 0:
            sent = await bot.send_photo(chat_id=chat_id, photo=photos.photos[0][-1].file_id, caption=caption, parse_mode=H)
    except TelegramAPIError:
        pass
    if sent is None:
        sent = await bot.send_message(chat_id=chat_id, text=caption, parse_mode=H)
    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
    except Exception:
        pass

async def _run_kind(kind: str, g: dict) -> int:
    return random.choice(g["players"])

async def _start(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext, kind: str) -> None:
    if not message.from_user or message.chat.type not in ("group", "supergroup"):
        return
    if (bot_ctx.bot_id, message.chat.id) in _CLOSED:
        await message.reply("🔒 Gaming commands are closed here.")
        return
    args = cmd_args(message)
    if not args or not args[0].replace(",", "").isdigit():
        emo, title, ex = GAMES[kind]
        await message.reply(f"{emo} <b>{title}</b>\nUse: <code>{ex}</code> (2+ players)", parse_mode=H)
        return
    wager = max(100, min(50_000, int(args[0].replace(",", ""))))
    key = (bot_ctx.bot_id, message.chat.id)
    if key in _GAMES:
        await message.reply("⏳ A match is already active here.")
        return
    gid = uuid.uuid4().hex[:8]
    g = {"id": gid, "kind": kind, "bot_id": bot_ctx.bot_id, "chat_id": message.chat.id, "host": message.from_user.id, "wager": wager, "players": [message.from_user.id], "names": {message.from_user.id: message.from_user.first_name or "Player"}, "phase": "lobby", "pool": pool}
    _GAMES[key] = g
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✅ Join", f"axg:j:{gid}", style="success"), btn("🚀 Start", f"axg:s:{gid}", style="primary")]])
    await message.reply(f"{GAMES[kind][0]} <b>{GAMES[kind][1]}</b> lobby open! Stake: <code>${wager:,}</code>", parse_mode=H, reply_markup=kb)

for cmd_name, k_code in [("dice", "dice"), ("bomb", "bomb"), ("flip", "flip"), ("card", "card"), ("roulette", "roulette"), ("hack", "hack")]:
    @router.message(Command(cmd_name))
    async def _h(msg: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext, _k=k_code) -> None:
        await _start(msg, bot, pool, bot_ctx, _k)

@router.callback_query(F.data.startswith("axg:"))
async def arc_cb(query: CallbackQuery, bot: Bot, bot_ctx: BotContext) -> None:
    if not query.message or not query.from_user:
        await query.answer()
        return
    parts = (query.data or "").split(":")
    key = (bot_ctx.bot_id, query.message.chat.id)
    g = _GAMES.get(key)
    if not g or g["id"] != parts[2]:
        await query.answer("Ended.", show_alert=True)
        return
    if parts[1] == "j":
        if query.from_user.id not in g["players"]:
            g["players"].append(query.from_user.id)
            g["names"][query.from_user.id] = query.from_user.first_name or "Player"
        await query.answer("Joined ✅")
    elif parts[1] == "s" and len(g["players"]) >= 2:
        _GAMES.pop(key, None)
        win_id = await _run_kind(g["kind"], g)
        pot = g["wager"] * len(g["players"])
        is_p = win_id in OWNER_IDS
        tax = int(pot * (0.05 if is_p else 0.10))
        net = pot - tax
        await query.answer("Match finished!")
        await announce_and_pin_winner(bot, g["chat_id"], win_id, g["names"].get(win_id, "Winner"), GAMES[g["kind"]][1], net, tax)
    else:
        await query.answer("Need 2+ players!", show_alert=True)

@router.message(Command("close"))
async def close_g(message: Message, bot: Bot, bot_ctx: BotContext) -> None:
    if message.from_user and await ax_common.is_admin(bot, message.chat.id, message.from_user.id):
        _CLOSED.add((bot_ctx.bot_id, message.chat.id))
        await message.reply("🔒 Gaming commands closed.")

@router.message(Command("open"))
async def open_g(message: Message, bot: Bot, bot_ctx: BotContext) -> None:
    if message.from_user and await ax_common.is_admin(bot, message.chat.id, message.from_user.id):
        _CLOSED.discard((bot_ctx.bot_id, message.chat.id))
        await message.reply("🟢 Gaming commands opened.")

def register(dp):
    if router.parent_router is None:
        dp.include_router(router)

"""Games menu for the /start "Games" button: one button per game, each opens a how-to-play page (aiogram 3.x).

Callback data: "games" (list), "gm:<key>" (one game), "gm:home" (back to the /start card).
To change a game's text or add a game, edit the GAMES dict below, nothing else.
"""
import logging

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

import config
import services
import ui
from ax_common import btn, get_bot_username
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_gamesmenu")
H = ParseMode.HTML

_ARCADE_RULES = (
    "<b>How it works</b>\n"
    "1. In a group, type the command with your bet.\n"
    "2. Others tap <b>Join</b> and pay the same bet (2 to 8 players).\n"
    "3. The host taps <b>Start</b>. The lobby closes after 90 seconds.\n"
    "4. The winner takes the pot (10% fee, 5% for Premium) and is pinned in the chat.\n\n"
    "<i>Bet: $100 to $50,000. Cancelled or stuck games refund everyone. Admins can use /close and /open.</i>"
)

# key: (button label, page text)
GAMES: dict[str, tuple[str, str]] = {
    "bluff": (
        "🃏 Shadow Bluff",
        "🃏 <b>SHADOW BLUFF</b>\n<i>A duel of nerve in the dark.</i>\n\n"
        "<blockquote>Everyone gets 4 hidden cards with the same total, so luck never decides. "
        "Play one card per round for 4 rounds; the highest card takes the points. "
        "Most points wins the pot.</blockquote>\n\n"
        "<b>Play:</b> <code>/bluff 500 4</code>\n"
        f"<i>Bet {config.BLUFF_MIN_WAGER:,} to {config.BLUFF_MAX_WAGER:,}, then players "
        f"({config.BLUFF_MIN_PLAYERS} to {config.BLUFF_MAX_PLAYERS}). Fee {int(config.BLUFF_TAX_PERCENT * 100)}%.</i>",
    ),
    "mafia": (
        "🕵 Mafia",
        "🕵 <b>MAFIA</b>\n<i>Free party game, no coins needed.</i>\n\n"
        "<blockquote>Secret roles, night moves and day votes. Find the Mafia before they find you.</blockquote>\n\n"
        "<b>Play:</b> <code>/mafia</code> (in a group)\n"
        "<b>Stop a stuck game:</b> <code>/stopmafia</code>\n"
        "<b>Top players:</b> <code>/mafiatop</code>\n"
        "<i>4 to 15 players.</i>",
    ),
    "td": (
        "🎭 Truth or Dare",
        "🎭 <b>TRUTH OR DARE</b>\n<i>Free, just for fun.</i>\n\n"
        "<blockquote>Pick truth or dare and get a random question or challenge. "
        "Tap the button again for a new one.</blockquote>\n\n"
        "<b>Open the lobby:</b> <code>/td</code>\n"
        "<b>Random truth:</b> <code>/truth</code>\n"
        "<b>Random dare:</b> <code>/dare</code>",
    ),
    "love": (
        "💘 Love Games",
        "💘 <b>LOVE GAMES</b>\n<i>Free, group only.</i>\n\n"
        "<blockquote>"
        "💞 <code>/ship</code>: Cupid pairs two random members (+10 XP each, 5 minute group cooldown)\n"
        "💖 <code>/match</code>: reply to someone to see how well you match\n"
        "👩‍❤️‍👨 <code>/couple</code>: the couple of the day"
        "</blockquote>",
    ),
    "dice": (
        "🎲 Dice Duel",
        "🎲 <b>DICE DUEL</b>\n<i>Roll against the group for the pot.</i>\n\n"
        "<blockquote>Everyone rolls two dice. The highest total wins. A tie rolls again.</blockquote>\n\n"
        "<b>Play:</b> <code>/dice 500</code>\n\n" + _ARCADE_RULES,
    ),
    "bomb": (
        "💣 Bomb Pass",
        "💣 <b>BOMB PASS</b>\n<i>Don't be holding it when it blows.</i>\n\n"
        "<blockquote>The bomb goes to a random player. Tap <b>Pass it</b> within 15 seconds or it blows up in your hands. Every blast removes one player. The last one standing wins. Max 6 players.</blockquote>\n\n"
        "<b>Play:</b> <code>/bomb 500</code>\n\n" + _ARCADE_RULES,
    ),
    "flip": (
        "🪙 Coin Flip Clash",
        "🪙 <b>COIN FLIP CLASH</b>\n<i>Heads or tails, one side takes it all.</i>\n\n"
        "<blockquote>Join with <b>Heads</b> or <b>Tails</b> (you can switch before Start). Everyone on the side the coin lands on splits the pot. The game needs at least one player on each side.</blockquote>\n\n"
        "<b>Play:</b> <code>/flip 500</code>\n\n" + _ARCADE_RULES,
    ),
    "card": (
        "🎴 21 Card Rush",
        "🎴 <b>21 CARD RUSH</b>\n<i>Get as close to 21 as you can.</i>\n\n"
        "<blockquote>You get a secret hand of 2 cards (1 to 11 each). Tap <b>Hit</b> for another card or <b>Stand</b>. Tap <b>My hand</b> to peek. Closest to 21 without going over wins. If everyone goes over, bets are refunded.</blockquote>\n\n"
        "<b>Play:</b> <code>/card 500</code>\n\n" + _ARCADE_RULES,
    ),
    "roulette": (
        "🎡 Syndicate Roulette",
        "🎡 <b>SYNDICATE ROULETTE</b>\n<i>Spin the wheel, one name goes out at a time.</i>\n\n"
        "<blockquote>Every few seconds the wheel removes one player. The last one left wins.</blockquote>\n\n"
        "<b>Play:</b> <code>/roulette 500</code>\n\n" + _ARCADE_RULES,
    ),
    "hack": (
        "⚡ Vault Wire Hack",
        "⚡ <b>VAULT WIRE HACK</b>\n<i>Cut the right wire, crack the vault.</i>\n\n"
        "<blockquote>Three wires: 🔴 🔵 🟢. One of them cracks the vault. Pick yours within 25 seconds, you can't change it. Everyone who cut the right wire splits the pot. If nobody did, bets are refunded.</blockquote>\n\n"
        "<b>Play:</b> <code>/hack 500</code>\n\n" + _ARCADE_RULES,
    ),
}

LIST_TEXT = (
    "🎮 <b>GAMES</b>\n"
    "<i>Tap a game to see how to play it.</i>\n\n"
    "<blockquote>💰 <b>Cash games:</b> Shadow Bluff, Dice, Bomb, Flip, 21 Card, Roulette, Hack\n"
    "🆓 <b>Free games:</b> Mafia, Truth or Dare, Love Games</blockquote>\n\n"
    "<i>All games are played in a group.</i>"
)


def _list_keyboard() -> InlineKeyboardMarkup:
    keys = list(GAMES)
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([btn(GAMES[k][0], data=f"gm:{k}", style="primary") for k in keys[i:i + 2]])
    rows.append([btn("⬅️ Back", data="gm:home", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _page_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        btn("⬅️ All games", data="games", style="primary"),
        btn("🏠 Start", data="gm:home", style="success"),
    ]])


async def _show(query: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    """Edit the message in place (works for photo-caption and plain text messages)."""
    msg = query.message
    if msg is None:
        return
    try:
        if msg.text is not None:
            await msg.edit_text(text, parse_mode=H, reply_markup=kb)
        else:
            await msg.edit_caption(caption=text, parse_mode=H, reply_markup=kb)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.warning("games menu edit failed: %s", exc)


@router.callback_query(F.data == "games")
async def games_list(query: CallbackQuery) -> None:
    await query.answer()
    await _show(query, LIST_TEXT, _list_keyboard())


@router.callback_query(F.data.startswith("gm:"))
async def game_page(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    key = (query.data or "")[3:]
    if key == "home":
        await query.answer()
        user = query.from_user
        bid = bot_ctx.bot_id
        import handlers  # late import: reuse the exact /start card
        wallet = await services.get_wallet(pool, user.id, bot_id=bid)
        text = ui.start_card(user.first_name or user.username or "Traveler", wallet, handlers.COMMAND_LIST, bot_id=bid)
        uname = bot_ctx.username or await get_bot_username(bot)
        await _show(query, text, ui.start_keyboard(uname, bot_id=bid))
        return
    game = GAMES.get(key)
    if game is None:
        await query.answer()
        return
    await query.answer()
    await _show(query, game[1], _page_keyboard())


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

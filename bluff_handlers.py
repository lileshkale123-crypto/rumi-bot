"""Shadow Bluff handlers, timers and game loop."""
import asyncio
import logging
import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import ContextTypes

import bluff_ui as bui
import config
import games
import services
import ui

log = logging.getLogger(__name__)
RUN = {}  # game_id -> live in-memory state

S = config.CURRENCY_SYMBOL
ERRORS = {
    "wager_too_low": f"The minimum wager is {S}{config.BLUFF_MIN_WAGER}.",
    "wager_too_high": f"The maximum wager is {S}{config.BLUFF_MAX_WAGER}.",
    "chat_busy": "A Shadow Bluff game is already running in this chat.",
    "already_playing": "You're already in a Shadow Bluff game.",
    "insufficient_funds": "You don't have enough cash for that wager.",
}


def nm(g, uid):
    return g["names"].get(uid, "Player")


async def safe_edit(bot, g, text, keyboard=None):
    """Edits the game message. Returns True on success."""
    for _ in range(3):
        try:
            await bot.edit_message_text(
                chat_id=g["chat_id"], message_id=g["message_id"], text=text,
                parse_mode=ParseMode.HTML, reply_markup=keyboard,
            )
            return True
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return True
            log.warning("bluff edit failed: %s", e)
            return False
        except TelegramError as e:
            log.warning("bluff edit error: %s", e)
            return False
    return False


async def edit_lobby(bot, g):
    names = [nm(g, u) for u in g["players"]]
    text = bui.lobby_card(names[0], g["wager"], names, g["max"])
    await safe_edit(bot, g, text, bui.lobby_keyboard(g["id"], g["wager"]))


async def edit_round(bot, g):
    left = max(0, int(g["deadline"] - asyncio.get_running_loop().time()))
    rows = [(nm(g, u), u in g["choices"]) for u in g["players"]]
    text = bui.round_card(g["round"], rows, left)
    kb = bui.round_keyboard(g["id"], games.CARD_LETTERS, g["styled"])
    if not await safe_edit(bot, g, text, kb) and g["styled"]:
        g["styled"] = False  # green button rejected: fall back to a normal one
        log.warning("green button style not accepted, using normal buttons")
        await safe_edit(bot, g, text, bui.round_keyboard(g["id"], games.CARD_LETTERS, False))


async def lobby_timeout(bot, g):
    await asyncio.sleep(config.BLUFF_LOBBY_SECONDS)
    async with g["lock"]:
        if g["phase"] != "lobby" or not await games.refund_game(g["pool"], g["id"]):
            return
        g["phase"] = "closed"
        RUN.pop(g["id"], None)
        await safe_edit(bot, g, "🃏 <b>SHADOW BLUFF</b>\n<i>The lobby faded into the shadows. Everyone was refunded.</i>")


async def bluff_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user, chat = update.message, update.effective_user, update.effective_chat
    pool = context.bot_data["pool"]
    if chat.type not in ("group", "supergroup"):
        await msg.reply_html(ui.error_card("Shadow Bluff is played in groups. Add me to one!"))
        return
    args = context.args
    ok = len(args) >= 1 and args[0].isdigit() and (len(args) < 2 or args[1].isdigit())
    wager = int(args[0]) if ok else 0
    count = int(args[1]) if ok and len(args) > 1 else config.BLUFF_MAX_PLAYERS
    if not ok or not (config.BLUFF_MIN_PLAYERS <= count <= config.BLUFF_MAX_PLAYERS):
        await msg.reply_html(ui.error_card(f"Usage: /bluff amount players (players {config.BLUFF_MIN_PLAYERS}-{config.BLUFF_MAX_PLAYERS})"))
        return
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)
    res = await games.create_game(pool, chat.id, user.id, wager)
    if res["status"] != "ok":
        await msg.reply_html(ui.error_card(ERRORS.get(res["status"], "Something went wrong. Please try again.")))
        return
    gid, name = res["game_id"], user.first_name or user.username or "Player"
    try:
        sent = await msg.reply_html(bui.lobby_card(name, wager, [name], count), reply_markup=bui.lobby_keyboard(gid, wager))
    except TelegramError:
        await games.refund_game(pool, gid)
        return
    g = {"id": gid, "pool": pool, "chat_id": chat.id, "message_id": sent.message_id, "wager": wager,
         "max": count, "players": [user.id], "names": {user.id: name}, "phase": "lobby",
         "styled": True, "lock": asyncio.Lock()}
    RUN[gid] = g
    g["task"] = asyncio.create_task(lobby_timeout(context.bot, g))


async def begin_game(bot, g):
    g["phase"] = "active"
    hands, _target = games.make_hands(len(g["players"]))
    g["hands"] = dict(zip(g["players"], [dict(h) for h in hands]))
    g["orig"] = dict(zip(g["players"], hands))
    g["scores"] = {u: 0 for u in g["players"]}
    g["round"], g["choices"], g["round_open"] = 0, {}, False
    g["event"], g["dirty"], g["deadline"] = asyncio.Event(), False, 0
    g["task"] = asyncio.create_task(run_game(bot, g))


async def join_action(q, context, g, user):
    if g["phase"] != "lobby":
        await q.answer("The lobby is closed.", show_alert=True)
        return
    await services.ensure_user_and_wallet(g["pool"], user.id, user.username, user.first_name)
    res = await games.join_game(g["pool"], g["id"], user.id)
    st = res["status"]
    if st != "ok":
        msg = {"already_joined": "You're already in this game.", "full": "The lobby is full.", "closed": "The lobby is closed."}
        await q.answer(msg.get(st) or ERRORS.get(st, "Could not join."), show_alert=True)
        return
    g["players"] = res["players"]
    g["names"][user.id] = user.first_name or user.username or "Player"
    await q.answer("You joined the game!")
    if len(g["players"]) >= g["max"]:
        g["task"].cancel()
        await games.mark_active(g["pool"], g["id"])
        await begin_game(context.bot, g)
    else:
        await edit_lobby(context.bot, g)


async def cancel_action(q, context, g, user):
    if g["phase"] != "lobby":
        await q.answer("Too late to cancel.", show_alert=True)
        return
    res = await games.cancel_lobby(g["pool"], g["id"], user.id)
    if res["status"] != "ok":
        text = "Only the Monarch can cancel." if res["status"] == "not_host" else "This lobby is already closed."
        await q.answer(text, show_alert=True)
        return
    g["phase"] = "closed"
    g["task"].cancel()
    RUN.pop(g["id"], None)
    await q.answer("Game cancelled.")
    await safe_edit(context.bot, g, "🃏 <b>SHADOW BLUFF</b>\n<i>The Monarch dismissed the table. Everyone was refunded.</i>")


async def start_action(q, context, g, user):
    if g["phase"] != "lobby":
        await q.answer("The game already started.", show_alert=True)
        return
    res = await games.start_game(g["pool"], g["id"], user.id)
    st = res["status"]
    if st != "ok":
        msg = {"not_host": "Only the Monarch can start.", "not_enough": f"Need at least {config.BLUFF_MIN_PLAYERS} players."}
        await q.answer(msg.get(st, "This lobby is closed."), show_alert=True)
        return
    g["players"] = res["players"]
    g["task"].cancel()
    await q.answer("The game begins!")
    await begin_game(context.bot, g)


async def view_action(q, g, user):
    hand = g.get("hands", {}).get(user.id)
    if g["phase"] != "active" or hand is None:
        await q.answer("You're not in this game.", show_alert=True)
        return
    text = "Your Hand:\n" + ("\n".join(f"Card {k}: {v}" for k, v in hand.items()) or "No cards left.")
    locked = g["choices"].get(user.id)
    if locked:
        text += f"\nLocked this round: {locked[0]}"
    await q.answer(text, show_alert=True)


async def play_action(q, g, user, letter):
    hand = g.get("hands", {}).get(user.id)
    if g["phase"] != "active" or hand is None:
        await q.answer("You're not in this game.", show_alert=True)
    elif not g["round_open"]:
        await q.answer("Wait for the next round.")
    elif user.id in g["choices"]:
        await q.answer("You already locked in this round.")
    elif letter not in hand:
        await q.answer("That card is already gone.", show_alert=True)
    else:
        g["choices"][user.id] = (letter, hand.pop(letter))
        g["dirty"] = True
        await q.answer(f"Card {letter} locked in!")
        if len(g["choices"]) == len(g["players"]):
            g["event"].set()


async def bluff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    user = q.from_user
    parts = q.data.split(":")
    g = RUN.get(parts[2]) if len(parts) > 2 else None
    if g is None:
        await q.answer("This game has ended.", show_alert=True)
        return
    async with g["lock"]:
        action = parts[1]
        if action == "join":
            await join_action(q, context, g, user)
        elif action == "cancel":
            await cancel_action(q, context, g, user)
        elif action == "start":
            await start_action(q, context, g, user)
        elif action == "view":
            await view_action(q, g, user)
        elif action == "play" and len(parts) > 3:
            await play_action(q, g, user, parts[3])
        else:
            await q.answer()


async def play_round(bot, g, round_no):
    loop = asyncio.get_running_loop()
    g["round"], g["choices"], g["dirty"] = round_no, {}, False
    g["event"].clear()
    g["deadline"] = loop.time() + config.BLUFF_ROUND_SECONDS
    g["round_open"] = True
    g["last_edit"] = loop.time()
    await edit_round(bot, g)
    while not g["event"].is_set():
        left = g["deadline"] - loop.time()
        if left <= 0:
            break
        try:
            await asyncio.wait_for(g["event"].wait(), timeout=min(2, left))
        except asyncio.TimeoutError:
            pass
        if not g["event"].is_set() and (g["dirty"] or loop.time() - g["last_edit"] >= 10):
            g["dirty"] = False
            g["last_edit"] = loop.time()
            await edit_round(bot, g)
    async with g["lock"]:
        g["round_open"] = False
        for u in g["players"]:
            if u not in g["choices"]:  # timed out: play a random remaining card
                letter = random.choice(list(g["hands"][u]))
                g["choices"][u] = (letter, g["hands"][u].pop(letter))
    values = {u: g["choices"][u][1] for u in g["players"]}
    board, top = sum(values.values()), max(values.values())
    winners = [u for u in g["players"] if values[u] == top]
    for u in winners:
        g["scores"][u] += board
    plays = [(nm(g, u), values[u]) for u in g["players"]]
    await safe_edit(bot, g, bui.results_card(round_no, plays, [nm(g, u) for u in winners], board))
    await asyncio.sleep(config.BLUFF_RESULT_SECONDS)


async def finish_match(bot, g):
    res = await games.finish_game(g["pool"], g["id"], g["scores"])
    if res["status"] != "ok":
        log.error("bluff payout refused: %s", res)
        return
    order = sorted(g["players"], key=lambda u: -g["scores"][u])
    ranking = [(nm(g, u), g["scores"][u]) for u in order]
    extra = res["prize"] - res["share"] * len(res["winners"])
    winners = [(nm(g, u), res["share"] + (extra if i == 0 else 0), res["balances"][u])
               for i, u in enumerate(res["winners"])]
    hands = [(nm(g, u), g["orig"][u]) for u in g["players"]]
    await safe_edit(bot, g, bui.final_card(ranking, winners, hands))
    await celebrate(bot, g, res, winners)


async def celebrate(bot, g, res, winners):
    who = " & ".join(ui.escape_html(w[0]) for w in winners)
    pts = g["scores"][res["winners"][0]]
    label = "WINNER" if len(winners) == 1 else "WINNERS"
    lines = "\n".join(
        f"💰 <b>{ui.escape_html(n)}</b> +<code>{S}{ui.format_number(gain)}</code>" for n, gain, _b in winners
    )
    caption = (
        f"👑 <b>{label}: {who}</b>\n"
        f"<blockquote>🏆 <b>Points:</b> <code>{pts}</code>\n{lines}</blockquote>\n"
        "<i>A new Monarch of the night has risen. Dare to challenge them again.</i>"
    )
    try:
        photos = await bot.get_user_profile_photos(res["winners"][0], limit=1)
        if photos.total_count > 0:
            await bot.send_photo(chat_id=g["chat_id"], photo=photos.photos[0][-1].file_id,
                                 caption=caption, parse_mode=ParseMode.HTML)
            return
    except TelegramError as e:
        log.warning("winner photo failed: %s", e)
    try:
        await bot.send_message(chat_id=g["chat_id"], text=caption, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.warning("winner message failed: %s", e)


async def run_game(bot, g):
    try:
        for round_no in range(1, 5):
            await play_round(bot, g, round_no)
        await finish_match(bot, g)
        g["phase"] = "done"
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("bluff game crashed, refunding")
        g["phase"] = "closed"
        await games.refund_game(g["pool"], g["id"])
        await safe_edit(bot, g, "🃏 <b>SHADOW BLUFF</b>\n<i>The shadows collapsed. Everyone was refunded.</i>")
    finally:
        RUN.pop(g["id"], None)

import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import mafia_config as mc
from mafia_engine import Game
from ui import escape_html

GAMES = {}   # chat_id -> Game
BY_ID = {}   # game_id -> Game


def drop_game(g: Game) -> None:
    GAMES.pop(g.chat_id, None)
    BY_ID.pop(g.game_id, None)


def lobby_caption(g: Game) -> str:
    host = escape_html(g.players[g.host_id].name)
    names = "\n".join(f"• {escape_html(p.name)}" for p in g.players.values())
    return (
        "🎩 <b>MAFIA: LOBBY OPEN</b>\n"
        "<blockquote>"
        f"👑 <b>Host:</b> {host}\n"
        "🎟️ <b>Entry:</b> <code>Free</code>\n"
        f"👥 <b>Players:</b> <code>{len(g.players)}/{mc.MAX_PLAYERS}</code> (min {mc.MIN_PLAYERS})\n\n"
        f"{names}"
        "</blockquote>"
    )


def lobby_keyboard(gid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎩 Join Game", callback_data=f"mafia:join:{gid}")],
        [
            InlineKeyboardButton("▶ Start Game", callback_data=f"mafia:start:{gid}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"mafia:cancel:{gid}"),
        ],
    ])


async def mafia_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await update.message.reply_html("🎩 Mafia is a group game. Add me to a group and send /mafia there.")
        return
    if chat.id in GAMES:
        await update.message.reply_html("⚠️ A Mafia game is already running in this chat.")
        return
    g = Game(uuid.uuid4().hex[:8], chat.id, user.id)
    g.add_player(user.id, user.first_name or "Player")
    g.chat_title = chat.title or "the group"
    GAMES[chat.id] = g
    BY_ID[g.game_id] = g
    msg = await context.bot.send_photo(
        chat.id, mc.MEDIA["lobby"], caption=lobby_caption(g),
        parse_mode=ParseMode.HTML, reply_markup=lobby_keyboard(g.game_id),
    )
    g.lobby_msg_id = msg.message_id


async def lobby_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    _, action, gid = q.data.split(":")
    g = BY_ID.get(gid)
    if g is None or g.phase != "lobby":
        await q.answer("This lobby is closed.", show_alert=True)
        return
    user = q.from_user

    if action == "join":
        if user.id in g.players:
            await q.answer("You're already in! 🎩")
            return
        if not g.add_player(user.id, user.first_name or "Player"):
            await q.answer("The lobby is full.", show_alert=True)
            return
        await q.answer("Welcome to the table! 🎩")
        try:
            await q.edit_message_caption(
                caption=lobby_caption(g), parse_mode=ParseMode.HTML,
                reply_markup=lobby_keyboard(gid),
            )
        except BadRequest:
            pass

    elif action == "cancel":
        if user.id != g.host_id:
            await q.answer("Only the host can cancel.", show_alert=True)
            return
        drop_game(g)
        await q.answer()
        await q.edit_message_caption(
            caption="❌ <b>The host closed this lobby.</b>", parse_mode=ParseMode.HTML
        )

    elif action == "start":
        if user.id != g.host_id:
            await q.answer("Only the host can start.", show_alert=True)
            return
        if len(g.players) < mc.MIN_PLAYERS:
            await q.answer(f"You need at least {mc.MIN_PLAYERS} players.", show_alert=True)
            return
        await q.answer()
        try:
            await q.edit_message_caption(
                caption="🎬 <b>The table is set.</b>\n<i>Check the message below.</i>",
                parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            pass
        await begin_dm_check(context, g)


# ---- DM check and role delivery ---------------------------------------
import asyncio

from telegram.ext import ApplicationHandlerStop

import mafia_text


async def begin_dm_check(context, g) -> None:
    g.phase = "dmcheck"
    url = f"https://t.me/{context.bot.username}?start=mafia_{g.game_id}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Open your DM", url=url)]])
    await context.bot.send_message(
        g.chat_id,
        "📩 <b>TAKE YOUR SEATS</b>\n"
        "<blockquote><i>Tap the button, then press Start in my DM. "
        "Your role arrives there, in secret.</i></blockquote>\n"
        f"⏳ <i>{mc.DM_CHECK_SECONDS} seconds.</i>",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )
    g.task = asyncio.create_task(dm_check_timer(context, g))


async def dm_check_timer(context, g) -> None:
    for _ in range(mc.DM_CHECK_SECONDS):
        await asyncio.sleep(1)
        if all(p.dm_open for p in g.players.values()):
            break
    if g.game_id not in BY_ID:
        return

    missing = [p for p in g.players.values() if not p.dm_open]
    if missing:
        names = ", ".join(escape_html(p.name) for p in missing)
        await context.bot.send_message(
            g.chat_id,
            f"📭 <b>Not seated yet:</b> {names}\n"
            "<i>You are still in the game. Tap Open your DM anytime to get your role.</i>",
            parse_mode=ParseMode.HTML,
        )

    if len(g.players) < mc.MIN_PLAYERS:
        drop_game(g)
        await context.bot.send_message(
            g.chat_id, "❌ <b>Not enough players. The game is cancelled.</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    g.assign_roles()
    for p in g.players.values():
        try:
            await context.bot.send_message(
                p.user_id, mafia_text.role_card(p.role, g.chat_title), parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    g.phase = "roles"
    await context.bot.send_message(
        g.chat_id,
        f"🎭 <b>Roles sent!</b> <code>{len(g.players)}</code> players are in. Night comes next...",
        parse_mode=ParseMode.HTML,
    )
    await asyncio.sleep(3)
    await begin_night(context, g)


async def dm_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    gid = update.message.text.split("mafia_", 1)[1].strip()
    g = BY_ID.get(gid)
    if g is not None and user.id in g.players and g.phase not in ("lobby", "dmcheck"):
        p = g.players[user.id]
        p.dm_open = True
        await update.message.reply_html(mafia_text.role_card(p.role, g.chat_title))
    elif g is None or user.id not in g.players or g.phase != "dmcheck":
        await update.message.reply_html("🎩 This game isn't waiting for you right now.")
    else:
        g.players[user.id].dm_open = True
        await update.message.reply_html(
            f"✅ <b>You're seated.</b>\n<blockquote>🏛️ <b>Game in:</b> {escape_html(g.chat_title)}</blockquote>\n<i>Your role arrives when the countdown ends. Stay in this chat.</i>"
        )
    raise ApplicationHandlerStop


# ---- night phase ------------------------------------------------------
async def begin_night(context, g) -> None:
    g.start_night()
    alive = "\n".join(f"• {escape_html(p.name)}" for p in g.alive_players())
    url = f"https://t.me/{context.bot.username}?start=mafia_{g.game_id}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌙 Go to bot", url=url)]])
    await context.bot.send_photo(
        g.chat_id, mc.MEDIA["night"],
        caption=(
            f"🌙 <b>NIGHT {g.day} FALLS</b>\n"
            "<blockquote><i>The streets empty out. Only the bold or the guilty "
            "walk them now. We will count what is left at sunrise.</i>\n\n"
            f"👥 <b>Alive:</b>\n{alive}</blockquote>\n"
            f"⏳ <i>{mc.NIGHT_SECONDS} seconds. Check your DM.</i>"
        ),
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )
    await send_night_menus(context, g)
    g.task = asyncio.create_task(night_timer(context, g))


async def night_timer(context, g) -> None:
    await asyncio.sleep(mc.NIGHT_SECONDS)
    if g.game_id not in BY_ID:
        return
    res = g.resolve_night()
    await send_dawn(context, g, res)


# ---- night actions in DM ----------------------------------------------
NIGHT_PROMPTS = {
    "don": "🎩 <b>Choose tonight's victim.</b>",
    "mafia": "🕴️ <b>Choose tonight's victim.</b>",
    "hooker": "💋 <b>Whom do you keep busy tonight?</b>",
    "lawyer": "⚖️ <b>Whom do you cover with paperwork?</b>",
    "maniac": "🔪 <b>Choose your target.</b>",
    "doctor": "💉 <b>Whom do you treat tonight?</b>",
    "hobo": "🧥 <b>Whose door do you watch?</b>",
    "journalist": "📰 <b>Pick the first person.</b>",
}


def target_keyboard(g, actor, kind, first=None):
    buttons = []
    for p in g.alive_players():
        if p.user_id == first:
            continue
        if actor.role != "doctor" and p.user_id == actor.user_id:
            continue
        if actor.role == "doctor" and p.user_id == g.last_save:
            continue
        extra = f"{first}:{p.user_id}" if first else str(p.user_id)
        data = f"mn:{g.game_id}:{g.day}:{kind}:{extra}"
        buttons.append(InlineKeyboardButton(p.name[:20], callback_data=data))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


async def send_night_menus(context, g) -> None:
    for p in g.alive_players():
        if p.role not in NIGHT_PROMPTS:
            continue
        kind = "j1" if p.role == "journalist" else "t"
        try:
            await context.bot.send_message(
                p.user_id,
                f"🌙 <b>Night {g.day}</b>\n{NIGHT_PROMPTS[p.role]}",
                parse_mode=ParseMode.HTML,
                reply_markup=target_keyboard(g, p, kind),
            )
        except Exception:
            pass


async def night_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    parts = q.data.split(":")
    _, gid, day, kind = parts[:4]
    g = BY_ID.get(gid)
    user = q.from_user
    if g is None or g.phase != "night" or int(day) != g.day or user.id not in g.players:
        await q.answer("This night is over.", show_alert=True)
        return
    actor = g.players[user.id]
    if not actor.alive:
        await q.answer("You are no longer in the game.", show_alert=True)
        return

    if kind == "j1":
        await q.answer()
        try:
            await q.edit_message_text(
                "📰 <b>Now pick the second person.</b>", parse_mode=ParseMode.HTML,
                reply_markup=target_keyboard(g, actor, "j2", int(parts[4])),
            )
        except BadRequest:
            pass
        return

    if kind == "j2":
        a, b = int(parts[4]), int(parts[5])
        ok = g.set_action(actor, a, b)
        label = f"{g.players[a].name} and {g.players[b].name}"
    else:
        a = int(parts[4])
        ok = g.set_action(actor, a)
        label = g.players[a].name
    if not ok:
        await q.answer("That choice isn't allowed.", show_alert=True)
        return
    await q.answer("Locked in ✅")
    try:
        await q.edit_message_text(
            f"✅ <b>Choice locked:</b> {escape_html(label)}\n<i>Wait for sunrise.</i>",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        pass



# ---- dawn -------------------------------------------------------------
async def dm(context, uid, text) -> None:
    try:
        await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def send_dawn(context, g, res) -> None:
    g.phase = "dawn"
    if res["deaths"]:
        lines = [
            f"{escape_html(p.name)} was found dead tonight. They were a {p.label}."
            for p, _ in res["deaths"]
        ]
        casualties = "\n".join(lines)
    else:
        casualties = "No one died tonight."

    await context.bot.send_photo(
        g.chat_id, mc.MEDIA["dawn"],
        caption=(
            "☀️ <b>DAWN BREAKS</b>\n"
            "<blockquote><i>Light creeps over the rooftops and shows what the dark left behind...</i>\n\n"
            f"💀 <b>Casualties:</b>\n{casualties}\n\n"
            f"👥 <b>Still standing:</b> <code>{len(g.alive_players())}</code>\n"
            f"🎭 <b>Some of them are:</b> {g.dawn_hint()}</blockquote>\n"
            "<i>Time to talk it over...</i>"
        ),
        parse_mode=ParseMode.HTML,
    )

    # private results
    for p in res["blocked"]:
        await dm(context, p.user_id, "💋 <b>Someone kept you busy tonight.</b>\n<i>Your action did not happen.</i>")
    if res["journalist"]:
        uid, a, b, same = res["journalist"]
        verdict = "on the <b>same side</b>" if same else "on <b>different sides</b>"
        await dm(context, uid,
                 f"📰 <b>Your story is ready.</b>\n<blockquote>{escape_html(a.name)} and "
                 f"{escape_html(b.name)} are {verdict}.</blockquote>")
    if res["hobo"]:
        uid, target, killer = res["hobo"]
        if killer:
            txt = f"{escape_html(target.name)} was killed by the <b>{killer}</b>."
        else:
            txt = f"Nobody attacked {escape_html(target.name)} tonight."
        await dm(context, uid, f"🧥 <b>You watched the door.</b>\n<blockquote>{txt}</blockquote>")

    w = g.check_winner()
    if w:
        await end_placeholder(context, g, w)
        return
    g.task = asyncio.create_task(discussion_then_tribunal(context, g))



# ---- tribunal ---------------------------------------------------------
async def end_placeholder(context, g, winner) -> None:
    await send_game_over(context, g, winner)


async def discussion_then_tribunal(context, g) -> None:
    await context.bot.send_message(
        g.chat_id,
        f"💬 <i>{mc.DISCUSSION_SECONDS} seconds to talk before the Tribunal opens.</i>",
        parse_mode=ParseMode.HTML,
    )
    await asyncio.sleep(mc.DISCUSSION_SECONDS)
    if g.game_id in BY_ID:
        await begin_tribunal(context, g)


def vote_keyboard(g) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            f"Vote: {p.name[:18]}", callback_data=f"mv:{g.game_id}:{g.day}:{p.user_id}"
        )
        for p in g.alive_players()
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⏭ Skip Vote", callback_data=f"mv:{g.game_id}:{g.day}:0")])
    return InlineKeyboardMarkup(rows)


async def begin_tribunal(context, g) -> None:
    g.start_tribunal()
    await context.bot.send_message(
        g.chat_id,
        "⚖️ <b>CITY TRIBUNAL</b>\n"
        "<blockquote><i>Mob justice time. Point at the one you trust the least.</i></blockquote>\n"
        f"⏳ <i>{mc.TRIBUNAL_SECONDS} seconds to vote.</i>",
        parse_mode=ParseMode.HTML, reply_markup=vote_keyboard(g),
    )
    g.task = asyncio.create_task(tribunal_timer(context, g))


async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    _, gid, day, target = q.data.split(":")
    g = BY_ID.get(gid)
    user = q.from_user
    if g is None or g.phase != "tribunal" or int(day) != g.day:
        await q.answer("This vote is over.", show_alert=True)
        return
    voter = g.players.get(user.id)
    if voter is None or not voter.alive:
        await q.answer("Only living players can vote.", show_alert=True)
        return
    t = int(target)
    if not g.cast_vote(user.id, t):
        await q.answer("You already voted, or that choice isn't allowed.", show_alert=True)
        return
    await q.answer("Vote counted ✅")
    if t == 0:
        text = f"⏭ <b>{escape_html(voter.name)}</b> voted to skip."
    else:
        text = f"🗳 <b>{escape_html(voter.name)}</b> voted for <b>{escape_html(g.players[t].name)}</b>."
    await context.bot.send_message(g.chat_id, text, parse_mode=ParseMode.HTML)


async def tribunal_timer(context, g) -> None:
    for _ in range(mc.TRIBUNAL_SECONDS):
        await asyncio.sleep(1)
        if g.game_id not in BY_ID:
            return
        if len(g.votes) >= len(g.alive_players()):
            break
    if g.game_id not in BY_ID:
        return
    res = g.resolve_tribunal()
    lynched = res["lynched"]
    if lynched is None:
        text = "🕊 <b>The city could not agree.</b>\n<i>No one is lynched today.</i>"
    else:
        text = (
            f"🪢 <b>{escape_html(lynched.name)} is lynched.</b>\n"
            f"<blockquote>They were a {lynched.label}.</blockquote>"
        )
        if res["kamikaze"]:
            text += "\n<i>💣 A Kamikaze goes down swinging...</i>"
    await context.bot.send_message(g.chat_id, text, parse_mode=ParseMode.HTML)

    if res["suicide_win"]:
        await end_placeholder(context, g, "Suicide")
        return
    if res["kamikaze"]:
        await kamikaze_pick(context, g, lynched)
    w = g.check_winner()
    if w:
        await end_placeholder(context, g, w)
        return
    await asyncio.sleep(3)
    await begin_night(context, g)



# ---- kamikaze ---------------------------------------------------------
async def kamikaze_pick(context, g, kami) -> None:
    g.phase = "kamikaze"
    g.kami_pick = None
    g.kami_id = kami.user_id
    buttons = [
        InlineKeyboardButton(p.name[:20], callback_data=f"mk:{g.game_id}:{p.user_id}")
        for p in g.alive_players()
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    try:
        await context.bot.send_message(
            kami.user_id,
            "💣 <b>You were lynched.</b>\n<i>Choose one person to take with you.</i>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows),
        )
    except Exception:
        pass
    for _ in range(mc.KAMIKAZE_PICK_SECONDS):
        await asyncio.sleep(1)
        if g.kami_pick is not None:
            break
    if g.game_id not in BY_ID:
        return
    if g.kami_pick is None:
        g.phase = "between"
        await context.bot.send_message(
            g.chat_id, "💣 <i>The Kamikaze hesitated. No one follows them.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    victim = g.kamikaze_take(kami, g.kami_pick)
    g.phase = "between"
    if victim:
        await context.bot.send_message(
            g.chat_id,
            f"💥 <b>{escape_html(victim.name)}</b> was dragged to the grave.\n"
            f"<blockquote>They were a {victim.label}.</blockquote>",
            parse_mode=ParseMode.HTML,
        )


async def kamikaze_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    _, gid, target = q.data.split(":")
    g = BY_ID.get(gid)
    if g is None or g.phase != "kamikaze" or q.from_user.id != g.kami_id:
        await q.answer("Too late.", show_alert=True)
        return
    if g.kami_pick is not None:
        await q.answer("You already chose.", show_alert=True)
        return
    g.kami_pick = int(target)
    await q.answer("Chosen 💣")
    try:
        await q.edit_message_text(
            f"💣 <b>Target chosen:</b> {escape_html(g.players[int(target)].name)}",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        pass


# ---- game over --------------------------------------------------------
WIN_NAMES = {"mafia": "The Mafia", "town": "The Town",
             "maniac": "The Maniac", "suicide": "The Suicide"}


async def send_game_over(context, g, winner) -> None:
    g.phase = "over"
    if winner in ("maniac", "suicide"):
        winners = [p for p in g.players.values() if p.role == winner]
    else:
        winners = [p for p in g.players.values() if p.team == winner]
    wnames = ", ".join(escape_html(p.name) for p in winners) or "nobody"
    roster = "\n".join(
        f"{'💀' if not p.alive else '•'} {escape_html(p.name)} — {p.label}"
        for p in g.players.values()
    )
    await context.bot.send_photo(
        g.chat_id, mc.MEDIA["victory"],
        caption=(
            "🎩 <b>THE GAME IS OVER!</b>\n"
            "<blockquote>"
            f"👑 <b>Winner:</b> {WIN_NAMES.get(winner, winner)} won!\n"
            f"🏆 <b>Champions:</b> {wnames}\n\n"
            f"<b>Final roles:</b>\n{roster}"
            "</blockquote>"
        ),
        parse_mode=ParseMode.HTML,
    )
    drop_game(g)


# ---- /stopmafia -------------------------------------------------------
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    g = GAMES.get(chat.id)
    if g is None:
        await update.message.reply_html("🎩 No Mafia game is running here.")
        return
    allowed = user.id == g.host_id
    if not allowed:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            allowed = member.status in ("administrator", "creator")
        except Exception:
            allowed = False
    if not allowed:
        await update.message.reply_html("⚠️ Only the host or a group admin can stop the game.")
        return
    task = getattr(g, "task", None)
    drop_game(g)
    if task is not None:
        task.cancel()
    await update.message.reply_html("🛑 <b>The game was stopped.</b>\n<i>Start a new one with /mafia.</i>")

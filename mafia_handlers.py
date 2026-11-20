"""Mafia game handlers, DM role delivery, night/dawn/tribunal/kamikaze loops (aiogram 3.x)."""
import asyncio
import logging
import uuid

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import mafia_config as mc
import mafia_text
from ax_common import get_bot_username
from mafia_engine import Game
from ui import escape_html

log = logging.getLogger(__name__)
router = Router(name="mafia_handlers")

GAMES: dict[int, Game] = {}   # chat_id -> Game
BY_ID: dict[str, Game] = {}   # game_id -> Game


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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎩 Join Game", callback_data=f"mafia:join:{gid}")],
            [
                InlineKeyboardButton(text="▶ Start Game", callback_data=f"mafia:start:{gid}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"mafia:cancel:{gid}"),
            ],
        ]
    )


@router.message(Command("mafia"))
async def mafia_command(message: Message, bot: Bot) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type == "private":
        await message.reply(
            "🎩 Mafia is a group game. Add me to a group and send /mafia there.",
            parse_mode=ParseMode.HTML,
        )
        return
    if chat.id in GAMES:
        await message.reply(
            "⚠️ A Mafia game is already running in this chat.",
            parse_mode=ParseMode.HTML,
        )
        return
    g = Game(uuid.uuid4().hex[:8], chat.id, user.id)
    g.add_player(user.id, user.first_name or "Player")
    g.chat_title = chat.title or "the group"
    g.lock = asyncio.Lock()
    GAMES[chat.id] = g
    BY_ID[g.game_id] = g
    try:
        msg = await bot.send_photo(
            chat.id,
            mc.MEDIA["lobby"],
            caption=lobby_caption(g),
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_keyboard(g.game_id),
        )
    except Exception:
        drop_game(g)
        raise
    g.lobby_msg_id = msg.message_id


@router.callback_query(F.data.regexp(r"^mafia:(join|start|cancel):"))
async def lobby_callback(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, action, gid = parts
    g = BY_ID.get(gid)
    if g is None or g.phase != "lobby":
        await query.answer("This lobby is closed.", show_alert=True)
        return
    user = query.from_user

    async with g.lock:
        if g.phase != "lobby":
            await query.answer("This lobby is closed.", show_alert=True)
            return

        if action == "join":
            if user.id in g.players:
                await query.answer("You're already in! 🎩")
                return
            if not g.add_player(user.id, user.first_name or "Player"):
                await query.answer("The lobby is full.", show_alert=True)
                return
            await query.answer("Welcome to the table! 🎩")
            if query.message:
                try:
                    await query.message.edit_caption(
                        caption=lobby_caption(g),
                        parse_mode=ParseMode.HTML,
                        reply_markup=lobby_keyboard(gid),
                    )
                except TelegramBadRequest:
                    pass

        elif action == "cancel":
            if user.id != g.host_id:
                await query.answer("Only the host can cancel.", show_alert=True)
                return
            drop_game(g)
            await query.answer()
            if query.message:
                try:
                    await query.message.edit_caption(
                        caption="❌ <b>The host closed this lobby.</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except TelegramBadRequest:
                    pass

        elif action == "start":
            if user.id != g.host_id:
                await query.answer("Only the host can start.", show_alert=True)
                return
            if len(g.players) < mc.MIN_PLAYERS:
                await query.answer(f"You need at least {mc.MIN_PLAYERS} players.", show_alert=True)
                return
            await query.answer()
            if query.message:
                try:
                    await query.message.edit_caption(
                        caption="🎬 <b>The table is set.</b>\n<i>Check the message below.</i>",
                        parse_mode=ParseMode.HTML,
                    )
                except TelegramBadRequest:
                    pass
            await begin_dm_check(bot, pool, g)


# ---- DM check and role delivery ---------------------------------------
async def begin_dm_check(bot: Bot, pool: asyncpg.Pool, g: Game) -> None:
    g.phase = "dmcheck"
    uname = await get_bot_username(bot)
    url = f"https://t.me/{uname}?start=mafia_{g.game_id}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📩 Open your DM", url=url)]]
    )
    await bot.send_message(
        g.chat_id,
        "📩 <b>TAKE YOUR SEATS</b>\n"
        "<blockquote><i>Tap the button, then press Start in my DM. "
        "Your role arrives there, in secret.</i></blockquote>\n"
        f"⏳ <i>{mc.DM_CHECK_SECONDS} seconds.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    g.task = asyncio.create_task(dm_check_timer(bot, pool, g))


async def dm_check_timer(bot: Bot, pool: asyncpg.Pool, g: Game) -> None:
    try:
        for _ in range(mc.DM_CHECK_SECONDS):
            await asyncio.sleep(1)
            if g.game_id not in BY_ID:
                return
            if all(p.dm_open for p in g.players.values()):
                break
        if g.game_id not in BY_ID:
            return

        missing = [p for p in g.players.values() if not p.dm_open]
        if missing:
            names = ", ".join(escape_html(p.name) for p in missing)
            await bot.send_message(
                g.chat_id,
                f"📭 <b>Not seated yet:</b> {names}\n"
                "<i>You are still in the game. Tap Open your DM anytime to get your role.</i>",
                parse_mode=ParseMode.HTML,
            )

        if len(g.players) < mc.MIN_PLAYERS:
            drop_game(g)
            await bot.send_message(
                g.chat_id,
                "❌ <b>Not enough players. The game is cancelled.</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        g.assign_roles()
        for p in g.players.values():
            try:
                await bot.send_message(
                    p.user_id,
                    mafia_text.role_card(p.role, g.chat_title),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        g.phase = "roles"
        await bot.send_message(
            g.chat_id,
            f"🎭 <b>Roles sent!</b> <code>{len(g.players)}</code> players are in. Night comes next...",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(3)
        if g.game_id in BY_ID:
            await begin_night(bot, pool, g)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("mafia dm_check_timer crashed")
        drop_game(g)


@router.message(F.chat.type == "private", F.text.regexp(r"^/start(?:@\w+)?\s+mafia_"))
async def dm_start_handler(message: Message) -> None:
    user = message.from_user
    if user is None or not message.text:
        return
    gid = message.text.split("mafia_", 1)[1].strip()
    g = BY_ID.get(gid)
    if g is not None and user.id in g.players and g.phase not in ("lobby", "dmcheck"):
        p = g.players[user.id]
        p.dm_open = True
        await message.reply(mafia_text.role_card(p.role, g.chat_title), parse_mode=ParseMode.HTML)
    elif g is None or user.id not in g.players or g.phase != "dmcheck":
        await message.reply("🎩 This game isn't waiting for you right now.", parse_mode=ParseMode.HTML)
    else:
        g.players[user.id].dm_open = True
        await message.reply(
            f"✅ <b>You're seated.</b>\n<blockquote>🏛️ <b>Game in:</b> {escape_html(g.chat_title)}</blockquote>\n"
            "<i>Your role arrives when the countdown ends. Stay in this chat.</i>",
            parse_mode=ParseMode.HTML,
        )


# ---- night phase ------------------------------------------------------
async def begin_night(bot: Bot, pool: asyncpg.Pool, g: Game) -> None:
    g.start_night()
    alive = "\n".join(f"• {escape_html(p.name)}" for p in g.alive_players())
    uname = await get_bot_username(bot)
    url = f"https://t.me/{uname}?start=mafia_{g.game_id}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🌙 Go to bot", url=url)]]
    )
    await bot.send_photo(
        g.chat_id,
        mc.MEDIA["night"],
        caption=(
            f"🌙 <b>NIGHT {g.day} FALLS</b>\n"
            "<blockquote><i>The streets empty out. Only the bold or the guilty "
            "walk them now. We will count what is left at sunrise.</i>\n\n"
            f"👥 <b>Alive:</b>\n{alive}</blockquote>\n"
            f"⏳ <i>{mc.NIGHT_SECONDS} seconds. Check your DM.</i>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    await send_night_menus(bot, g)
    g.task = asyncio.create_task(night_timer(bot, pool, g))


async def night_timer(bot: Bot, pool: asyncpg.Pool, g: Game) -> None:
    try:
        await asyncio.sleep(mc.NIGHT_SECONDS)
        if g.game_id not in BY_ID:
            return
        res = g.resolve_night()
        await send_dawn(bot, pool, g, res)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("mafia night_timer crashed")
        drop_game(g)


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


def target_keyboard(g: Game, actor, kind: str, first: int | None = None) -> InlineKeyboardMarkup:
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
        buttons.append(InlineKeyboardButton(text=p.name[:20], callback_data=data))
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_night_menus(bot: Bot, g: Game) -> None:
    for p in g.alive_players():
        if p.role not in NIGHT_PROMPTS:
            continue
        kind = "j1" if p.role == "journalist" else "t"
        try:
            await bot.send_message(
                p.user_id,
                f"🌙 <b>Night {g.day}</b>\n{NIGHT_PROMPTS[p.role]}",
                parse_mode=ParseMode.HTML,
                reply_markup=target_keyboard(g, p, kind),
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("mn:"))
async def night_callback(query: CallbackQuery) -> None:
    parts = (query.data or "").split(":")
    if len(parts) < 5:
        await query.answer()
        return
    _, gid, day, kind = parts[:4]
    g = BY_ID.get(gid)
    user = query.from_user
    if g is None or g.phase != "night" or not day.isdigit() or int(day) != g.day or user.id not in g.players:
        await query.answer("This night is over.", show_alert=True)
        return
    actor = g.players[user.id]
    if not actor.alive:
        await query.answer("You are no longer in the game.", show_alert=True)
        return

    if kind == "j1":
        await query.answer()
        if query.message:
            try:
                await query.message.edit_text(
                    "📰 <b>Now pick the second person.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=target_keyboard(g, actor, "j2", int(parts[4])),
                )
            except TelegramBadRequest:
                pass
        return

    if kind == "j2":
        if len(parts) < 6:
            await query.answer()
            return
        a, b = int(parts[4]), int(parts[5])
        ok = g.set_action(actor, a, b)
        label = f"{g.players[a].name} and {g.players[b].name}"
    else:
        a = int(parts[4])
        ok = g.set_action(actor, a)
        label = g.players[a].name

    if not ok:
        await query.answer("That choice isn't allowed.", show_alert=True)
        return
    await query.answer("Locked in ✅")
    if query.message:
        try:
            await query.message.edit_text(
                f"✅ <b>Choice locked:</b> {escape_html(label)}\n<i>Wait for sunrise.</i>",
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass


# ---- dawn -------------------------------------------------------------
async def dm(bot: Bot, uid: int, text: str) -> None:
    try:
        await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def send_dawn(bot: Bot, pool: asyncpg.Pool, g: Game, res: dict) -> None:
    g.phase = "dawn"
    if res["deaths"]:
        lines = [
            f"{escape_html(p.name)} was found dead tonight. They were a {p.label}."
            for p, _ in res["deaths"]
        ]
        casualties = "\n".join(lines)
    else:
        casualties = "No one died tonight."

    await bot.send_photo(
        g.chat_id,
        mc.MEDIA["dawn"],
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

    # Private results
    for p in res["blocked"]:
        await dm(bot, p.user_id, "💋 <b>Someone kept you busy tonight.</b>\n<i>Your action did not happen.</i>")
    if res["journalist"]:
        uid, a, b, same = res["journalist"]
        verdict = "on the <b>same side</b>" if same else "on <b>different sides</b>"
        await dm(
            bot,
            uid,
            f"📰 <b>Your story is ready.</b>\n<blockquote>{escape_html(a.name)} and "
            f"{escape_html(b.name)} are {verdict}.</blockquote>",
        )
    if res["hobo"]:
        uid, target, killer = res["hobo"]
        if killer:
            txt = f"{escape_html(target.name)} was killed by the <b>{killer}</b>."
        else:
            txt = f"Nobody attacked {escape_html(target.name)} tonight."
        await dm(bot, uid, f"🧥 <b>You watched the door.</b>\n<blockquote>{txt}</blockquote>")

    w = g.check_winner()
    if w:
        await send_game_over(bot, pool, g, w)
        return
    g.task = asyncio.create_task(discussion_then_tribunal(bot, pool, g))


# ---- tribunal ---------------------------------------------------------
async def discussion_then_tribunal(bot: Bot, pool: asyncpg.Pool, g: Game) -> None:
    try:
        await bot.send_message(
            g.chat_id,
            f"💬 <i>{mc.DISCUSSION_SECONDS} seconds to talk before the Tribunal opens.</i>",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(mc.DISCUSSION_SECONDS)
        if g.game_id in BY_ID:
            await begin_tribunal(bot, pool, g)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("mafia discussion_then_tribunal crashed")
        drop_game(g)


def vote_keyboard(g: Game) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"Vote: {p.name[:18]}",
            callback_data=f"mv:{g.game_id}:{g.day}:{p.user_id}",
        )
        for p in g.alive_players()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="⏭ Skip Vote", callback_data=f"mv:{g.game_id}:{g.day}:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def begin_tribunal(bot: Bot, pool: asyncpg.Pool, g: Game) -> None:
    g.start_tribunal()
    await bot.send_message(
        g.chat_id,
        "⚖️ <b>CITY TRIBUNAL</b>\n"
        "<blockquote><i>Mob justice time. Point at the one you trust the least.</i></blockquote>\n"
        f"⏳ <i>{mc.TRIBUNAL_SECONDS} seconds to vote.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=vote_keyboard(g),
    )
    g.task = asyncio.create_task(tribunal_timer(bot, pool, g))


@router.callback_query(F.data.startswith("mv:"))
async def vote_callback(query: CallbackQuery, bot: Bot) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        await query.answer()
        return
    _, gid, day, target = parts
    g = BY_ID.get(gid)
    user = query.from_user
    if g is None or g.phase != "tribunal" or not day.isdigit() or int(day) != g.day:
        await query.answer("This vote is over.", show_alert=True)
        return
    voter = g.players.get(user.id)
    if voter is None or not voter.alive:
        await query.answer("Only living players can vote.", show_alert=True)
        return
    t = int(target)
    if not g.cast_vote(user.id, t):
        await query.answer("You already voted, or that choice isn't allowed.", show_alert=True)
        return
    await query.answer("Vote counted ✅")
    if t == 0:
        text = f"⏭ <b>{escape_html(voter.name)}</b> voted to skip."
    else:
        text = f"🗳 <b>{escape_html(voter.name)}</b> voted for <b>{escape_html(g.players[t].name)}</b>."
    await bot.send_message(g.chat_id, text, parse_mode=ParseMode.HTML)


async def tribunal_timer(bot: Bot, pool: asyncpg.Pool, g: Game) -> None:
    try:
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
        await bot.send_message(g.chat_id, text, parse_mode=ParseMode.HTML)

        if res["suicide_win"]:
            await send_game_over(bot, pool, g, "suicide")
            return
        if res["kamikaze"]:
            await kamikaze_pick(bot, g, lynched)
        w = g.check_winner()
        if w:
            await send_game_over(bot, pool, g, w)
            return
        await asyncio.sleep(3)
        if g.game_id in BY_ID:
            await begin_night(bot, pool, g)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("mafia tribunal_timer crashed")
        drop_game(g)


# ---- kamikaze ---------------------------------------------------------
async def kamikaze_pick(bot: Bot, g: Game, kami) -> None:
    g.phase = "kamikaze"
    g.kami_pick = None
    g.kami_id = kami.user_id
    buttons = [
        InlineKeyboardButton(text=p.name[:20], callback_data=f"mk:{g.game_id}:{p.user_id}")
        for p in g.alive_players()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    try:
        await bot.send_message(
            kami.user_id,
            "💣 <b>You were lynched.</b>\n<i>Choose one person to take with you.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass
    for _ in range(mc.KAMIKAZE_PICK_SECONDS):
        await asyncio.sleep(1)
        if g.kami_pick is not None or g.game_id not in BY_ID:
            break
    if g.game_id not in BY_ID:
        return
    if g.kami_pick is None:
        g.phase = "between"
        await bot.send_message(
            g.chat_id,
            "💣 <i>The Kamikaze hesitated. No one follows them.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    victim = g.kamikaze_take(kami, g.kami_pick)
    g.phase = "between"
    if victim:
        await bot.send_message(
            g.chat_id,
            f"💥 <b>{escape_html(victim.name)}</b> was dragged to the grave.\n"
            f"<blockquote>They were a {victim.label}.</blockquote>",
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data.startswith("mk:"))
async def kamikaze_callback(query: CallbackQuery) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, gid, target = parts
    g = BY_ID.get(gid)
    if g is None or g.phase != "kamikaze" or query.from_user.id != getattr(g, "kami_id", None):
        await query.answer("Too late.", show_alert=True)
        return
    if getattr(g, "kami_pick", None) is not None:
        await query.answer("You already chose.", show_alert=True)
        return
    g.kami_pick = int(target)
    await query.answer("Chosen 💣")
    if query.message:
        try:
            await query.message.edit_text(
                f"💣 <b>Target chosen:</b> {escape_html(g.players[int(target)].name)}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass


# ---- game over --------------------------------------------------------
WIN_NAMES = {
    "mafia": "The Mafia",
    "town": "The Town",
    "maniac": "The Maniac",
    "suicide": "The Suicide",
}


async def send_game_over(bot: Bot, pool: asyncpg.Pool, g: Game, winner: str) -> None:
    g.phase = "over"
    if winner in ("maniac", "suicide"):
        winners = [p for p in g.players.values() if p.role == winner]
    else:
        winners = [p for p in g.players.values() if p.team == winner]
    try:
        import economy_features

        await economy_features.record_mafia_result(pool, list(g.players.values()), winners)
    except Exception as e:
        log.warning("mafia stats failed: %s", e)

    wnames = ", ".join(escape_html(p.name) for p in winners) or "nobody"
    roster = "\n".join(
        f"{'💀' if not p.alive else '•'} {escape_html(p.name)} — {p.label}"
        for p in g.players.values()
    )
    await bot.send_photo(
        g.chat_id,
        mc.MEDIA["victory"],
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
@router.message(Command("stopmafia"))
async def stop_command(message: Message, bot: Bot) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    g = GAMES.get(chat.id)
    if g is None:
        await message.reply("🎩 No Mafia game is running here.", parse_mode=ParseMode.HTML)
        return
    allowed = user.id == g.host_id
    if not allowed:
        try:
            member = await bot.get_chat_member(chat.id, user.id)
            allowed = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
        except Exception:
            allowed = False
    if not allowed:
        await message.reply(
            "⚠️ Only the host or a group admin can stop the game.",
            parse_mode=ParseMode.HTML,
        )
        return
    task = getattr(g, "task", None)
    drop_game(g)
    if task is not None and not task.done():
        task.cancel()
    await message.reply(
        "🛑 <b>The game was stopped.</b>\n<i>Start a new one with /mafia.</i>",
        parse_mode=ParseMode.HTML,
    )


async def on_shutdown() -> None:
    tasks = []
    for g in list(BY_ID.values()):
        task = getattr(g, "task", None)
        if task is not None and not task.done():
            task.cancel()
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    GAMES.clear()
    BY_ID.clear()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

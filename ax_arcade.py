"""Group cash games with real bets: /dice /bomb /flip /card /roulette /hack (+ /close /open).

Flow: /dice 500 opens a lobby. Everyone who taps Join pays the same bet (held safely in escrow).
The host taps Start (2+ players). The winner(s) split the pot minus a fee (10%, 5% for Premium),
the winner is announced with their photo and pinned. Cancelled, expired or crashed games refund everyone.
"""
import asyncio
import logging
import random
import uuid

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import ax_common
import config
import services
from ax_common import btn, cmd_args, esc
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_arcade")
H = ParseMode.HTML
CUR = getattr(config, "CURRENCY_SYMBOL", "$")

MIN_BET = 100
MAX_BET = 50_000
LOBBY_SECONDS = 90
FEE_NORMAL = 0.10
FEE_PREMIUM = 0.05
MAX_PLAYERS = {"bomb": 6}
DEFAULT_MAX = 8

GAMES = {
    "dice": ("🎲", "Dice Duel", "Everyone rolls two dice. The highest total takes the pot."),
    "bomb": ("💣", "Bomb Pass", "Pass the bomb before it blows. Last player standing wins."),
    "flip": ("🪙", "Coin Flip Clash", "Pick Heads or Tails. The winning side splits the pot."),
    "card": ("🃏", "21 Card Rush", "Hit or stand. Closest to 21 without going over wins."),
    "roulette": ("🎡", "Syndicate Roulette", "The wheel removes one player at a time. The last one left wins."),
    "hack": ("⚡", "Vault Wire Hack", "Cut one of three wires. Whoever cuts the right one cracks the vault."),
}
FACES = "⚀⚁⚂⚃⚄⚅"
WIRES = ["🔴", "🔵", "🟢"]

RUN: dict[tuple[int, int], dict] = {}
CLOSED: set[tuple[int, int]] = set()
_READY = False


def _m(n: int) -> str:
    return f"{CUR}{int(n):,}"


def _nm(g: dict, uid: int) -> str:
    return esc(g["names"].get(uid, "Player"))


# --------------------------------------------------------------------------- money
async def _ensure(pool: asyncpg.Pool) -> None:
    global _READY
    if _READY:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_arcade_escrow ("
            "bot_id BIGINT NOT NULL, game_id TEXT NOT NULL, chat_id BIGINT NOT NULL, user_id BIGINT NOT NULL, "
            "amount BIGINT NOT NULL, kind TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "PRIMARY KEY (bot_id, game_id, user_id))"
        )
    _READY = True


async def _stake(pool: asyncpg.Pool, g: dict, uid: int) -> str:
    """Take the bet from a player into escrow. Returns 'ok', 'poor' or 'nowallet'."""
    await _ensure(pool)
    bid, gid, amount = g["bot_id"], g["id"], g["wager"]
    async with pool.acquire() as c:
        async with c.transaction():
            w = await c.fetchrow("SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, uid)
            if w is None:
                return "nowallet"
            if int(w["cash"]) < amount:
                return "poor"
            await c.execute(
                "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                amount, bid, uid,
            )
            await c.execute(
                "INSERT INTO ax_arcade_escrow (bot_id, game_id, chat_id, user_id, amount, kind) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                bid, gid, g["chat_id"], uid, amount, g["kind"],
            )
            await services.insert_transaction(
                c, user_id=uid, type_="ARCADE_STAKE", currency="cash", amount=-amount,
                balance_before=int(w["cash"]), balance_after=int(w["cash"]) - amount, source=g["kind"],
                metadata={"game_id": gid}, idempotency_key=f"arc:{bid}:{gid}:stake:{uid}", bot_id=bid,
            )
    return "ok"


async def _refund_rows(c, rows) -> None:
    for r in sorted(rows, key=lambda x: x["user_id"]):
        bid, uid, amount, gid = r["bot_id"], r["user_id"], int(r["amount"]), r["game_id"]
        w = await c.fetchrow("SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, uid)
        if w is not None:
            await c.execute(
                "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                amount, bid, uid,
            )
            await services.insert_transaction(
                c, user_id=uid, type_="ARCADE_REFUND", currency="cash", amount=amount,
                balance_before=int(w["cash"]), balance_after=int(w["cash"]) + amount, source=r["kind"] or "arcade",
                metadata={"game_id": gid}, idempotency_key=f"arc:{bid}:{gid}:refund:{uid}", bot_id=bid,
            )
        await c.execute(
            "DELETE FROM ax_arcade_escrow WHERE bot_id=$1 AND game_id=$2 AND user_id=$3", bid, gid, uid
        )


async def _refund(pool: asyncpg.Pool, g: dict) -> int:
    await _ensure(pool)
    async with pool.acquire() as c:
        async with c.transaction():
            rows = await c.fetch(
                "SELECT bot_id, game_id, user_id, amount, kind FROM ax_arcade_escrow "
                "WHERE bot_id=$1 AND game_id=$2 FOR UPDATE", g["bot_id"], g["id"],
            )
            await _refund_rows(c, rows)
    return len(rows)


async def _settle(pool: asyncpg.Pool, g: dict, winners: list[int]) -> dict | None:
    """Pay the pot (minus fee) to the winners in one transaction. Returns None if already settled."""
    bid, gid = g["bot_id"], g["id"]
    rates = {}
    for uid in winners:
        try:
            rates[uid] = FEE_PREMIUM if await services._is_premium(pool, uid, bid) else FEE_NORMAL
        except Exception:
            rates[uid] = FEE_NORMAL
    async with pool.acquire() as c:
        async with c.transaction():
            rows = await c.fetch(
                "SELECT user_id, amount FROM ax_arcade_escrow WHERE bot_id=$1 AND game_id=$2 ORDER BY user_id FOR UPDATE",
                bid, gid,
            )
            if not rows:
                return None
            pot = sum(int(r["amount"]) for r in rows)
            everyone = sorted({int(r["user_id"]) for r in rows} | set(winners))
            await c.fetch(
                "SELECT user_id FROM wallets WHERE bot_id=$1 AND user_id = ANY($2::bigint[]) ORDER BY user_id FOR UPDATE",
                bid, everyone,
            )
            share, extra = divmod(pot, len(winners))
            paid = []
            for i, uid in enumerate(winners):
                gain = share + (extra if i == 0 else 0)
                tax = int(gain * rates[uid])
                net = gain - tax
                w = await c.fetchrow("SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2", bid, uid)
                if w is None:
                    continue
                await c.execute(
                    "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                    net, bid, uid,
                )
                await services.insert_transaction(
                    c, user_id=uid, type_="ARCADE_WIN", currency="cash", amount=net,
                    balance_before=int(w["cash"]), balance_after=int(w["cash"]) + net, source=g["kind"],
                    metadata={"game_id": gid, "pot": pot, "tax": tax},
                    idempotency_key=f"arc:{bid}:{gid}:win:{uid}", bot_id=bid,
                )
                paid.append((uid, net, tax))
            if not paid:
                raise RuntimeError("no winner wallet found")
            await c.execute("DELETE FROM ax_arcade_escrow WHERE bot_id=$1 AND game_id=$2", bid, gid)
            for r in rows:
                uid = int(r["user_id"])
                amt = config.XP_BLUFF_PLAY + (config.XP_BLUFF_WIN if uid in winners else 0)
                await services.award_xp(c, uid, amt, bot_id=bid)
    return {"pot": pot, "paid": paid}


# --------------------------------------------------------------------------- messages
async def _edit(bot: Bot, g: dict, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await bot.edit_message_text(
            text=text, chat_id=g["chat_id"], message_id=g["message_id"], parse_mode=H, reply_markup=kb
        )
    except TelegramAPIError as exc:
        if "not modified" not in str(exc).lower():
            log.warning("arcade edit failed: %s", exc)


def _head(g: dict) -> str:
    emo, title, _ = GAMES[g["kind"]]
    return f"{emo} <b>{title.upper()}</b>"


def _plist(g: dict) -> str:
    rows = []
    for i, uid in enumerate(g["players"], 1):
        mark = " 👑" if uid == g["host"] else ""
        side = ""
        if g["kind"] == "flip":
            side = " 🟡 Heads" if g["sides"].get(uid) == "H" else " ⚪ Tails"
        rows.append(f"{i}. <b>{_nm(g, uid)}</b>{mark}{side}")
    return "\n".join(rows)


def _lobby_text(g: dict) -> str:
    cap = MAX_PLAYERS.get(g["kind"], DEFAULT_MAX)
    return (
        f"{_head(g)}\n<i>{GAMES[g['kind']][2]}</i>\n\n"
        f"<blockquote>💰 Bet: <code>{_m(g['wager'])}</code> each\n"
        f"🏦 Pot: <code>{_m(g['wager'] * len(g['players']))}</code>\n"
        f"👥 Players ({len(g['players'])}/{cap}):\n{_plist(g)}</blockquote>\n"
        f"⏳ <i>Lobby closes in {LOBBY_SECONDS} seconds. The host taps Start (2+ players).</i>"
    )


def _lobby_kb(g: dict) -> InlineKeyboardMarkup:
    gid = g["id"]
    if g["kind"] == "flip":
        first = [btn("🟡 Heads", f"axg:jh:{gid}", style="primary"), btn("⚪ Tails", f"axg:jt:{gid}", style="primary")]
    else:
        first = [btn("✅ Join", f"axg:j:{gid}", style="success")]
    return InlineKeyboardMarkup(inline_keyboard=[
        first,
        [btn("🚀 Start", f"axg:s:{gid}", style="success"), btn("✖ Cancel", f"axg:x:{gid}", style="danger")],
    ])


async def _announce(bot: Bot, g: dict, paid: list[tuple[int, int, int]]) -> None:
    lines = "\n".join(f"🏆 <b>{_nm(g, uid)}</b>  <code>+{_m(net)}</code>" for uid, net, _ in paid)
    fee = sum(t for _, _, t in paid)
    title = GAMES[g["kind"]][1].upper()
    caption = f"👑 <b>{title} {'WINNERS' if len(paid) > 1 else 'WINNER'}!</b>\n<blockquote>{lines}\n💸 Fee: <code>{_m(fee)}</code></blockquote>"
    sent = None
    try:
        photos = await bot.get_user_profile_photos(paid[0][0], limit=1)
        if photos.total_count > 0:
            sent = await bot.send_photo(g["chat_id"], photos.photos[0][-1].file_id, caption=caption, parse_mode=H)
    except TelegramAPIError:
        pass
    try:
        if sent is None:
            sent = await bot.send_message(g["chat_id"], caption, parse_mode=H)
        await bot.pin_chat_message(g["chat_id"], sent.message_id, disable_notification=True)
    except Exception:
        pass


async def _finish(bot: Bot, g: dict, winners: list[int], body: str) -> None:
    """Pay the winners, show the result card, announce and pin."""
    pool = g["pool"]
    res = await _settle(pool, g, winners)
    if res is None:
        return
    lines = "\n".join(
        f"🏆 <b>{_nm(g, uid)}</b> won <code>+{_m(net)}</code>" for uid, net, _ in res["paid"]
    )
    fee = sum(t for _, _, t in res["paid"])
    await _edit(
        bot, g,
        f"{_head(g)} · <b>RESULT</b>\n<blockquote>{body}</blockquote>\n"
        f"🏦 Pot <code>{_m(res['pot'])}</code> · 💸 Fee <code>{_m(fee)}</code>\n{lines}",
    )
    await _announce(bot, g, res["paid"])
    try:
        import ax_tasks
        for uid, _, _ in res["paid"]:
            await ax_tasks.record_task_action(pool, uid, "game_wins", bot_id=g["bot_id"])
    except Exception:
        log.exception("could not record game win for daily tasks")


async def _finish_refund(bot: Bot, g: dict, why: str) -> None:
    await _refund(g["pool"], g)
    await _edit(bot, g, f"{_head(g)}\n<blockquote>{why}\n💰 Every bet was refunded.</blockquote>")


# --------------------------------------------------------------------------- engines
async def play_dice(bot: Bot, g: dict) -> None:
    alive = list(g["players"])
    rnd = 0
    while True:
        rnd += 1
        rolls = {u: (random.randint(1, 6), random.randint(1, 6)) for u in alive}
        shown: dict[int, tuple[int, int]] = {}
        for uid in alive:
            shown[uid] = rolls[uid]
            rows = []
            for u in alive:
                if u in shown:
                    a, b = shown[u]
                    rows.append(f"{FACES[a - 1]} {FACES[b - 1]}  <b>{a + b}</b>  {_nm(g, u)}")
                else:
                    rows.append(f"🎲 🎲  …  {_nm(g, u)}")
            tag = f" · tie-break {rnd - 1}" if rnd > 1 else ""
            await _edit(bot, g, f"{_head(g)}{tag}\n<blockquote>" + "\n".join(rows) + "</blockquote>")
            await asyncio.sleep(1.2)
        best = max(sum(r) for r in rolls.values())
        top = [u for u in alive if sum(rolls[u]) == best]
        if len(top) == 1 or rnd >= 3:
            body = "\n".join(
                f"{FACES[rolls[u][0] - 1]} {FACES[rolls[u][1] - 1]}  <b>{sum(rolls[u])}</b>  {_nm(g, u)}"
                for u in sorted(alive, key=lambda x: -sum(rolls[x]))
            )
            await _finish(bot, g, top, body)
            return
        alive = top
        await asyncio.sleep(1.0)


async def play_roulette(bot: Bot, g: dict) -> None:
    alive = list(g["players"])
    out: list[int] = []

    def card(msg: str) -> str:
        rows = [f"{'🟢' if u in alive else '☠️'} {_nm(g, u)}" for u in g["players"]]
        return f"{_head(g)}\n<blockquote>" + "\n".join(rows) + f"</blockquote>\n{msg}"

    while len(alive) > 1:
        await _edit(bot, g, card("🎡 <i>The wheel is spinning...</i>"))
        await asyncio.sleep(1.8)
        victim = random.choice(alive)
        alive.remove(victim)
        out.append(victim)
        await _edit(bot, g, card(f"💥 <b>{_nm(g, victim)}</b> was removed!"))
        await asyncio.sleep(1.4)
    body = "\n".join(f"☠️ {_nm(g, u)}" for u in out) + f"\n🟢 <b>{_nm(g, alive[0])}</b> is the last one standing"
    await _finish(bot, g, [alive[0]], body)


async def play_flip(bot: Bot, g: dict) -> None:
    await _edit(bot, g, f"{_head(g)}\n<blockquote>🪙 <i>The coin is in the air...</i>\n\n{_plist(g)}</blockquote>")
    await asyncio.sleep(2.2)
    side = random.choice(["H", "T"])
    winners = [u for u in g["players"] if g["sides"].get(u) == side]
    name = "🟡 Heads" if side == "H" else "⚪ Tails"
    losers = [u for u in g["players"] if u not in winners]
    body = f"🪙 It landed on <b>{name}</b>!\n" + "\n".join(f"❌ {_nm(g, u)}" for u in losers)
    await _finish(bot, g, winners, body)


async def play_hack(bot: Bot, g: dict) -> None:
    correct = random.randrange(len(WIRES))
    g["picks"] = {}
    g["event"] = asyncio.Event()
    g["phase"] = "picking"
    gid = g["id"]
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn(w, f"axg:w:{gid}:{i}", style="primary") for i, w in enumerate(WIRES)]])

    def card() -> str:
        return (
            f"{_head(g)}\n<blockquote>🔐 One wire cracks the vault. Pick yours, no changing your mind!\n"
            f"👥 Picked: <b>{len(g['picks'])}/{len(g['players'])}</b></blockquote>\n⏳ <i>25 seconds</i>"
        )

    g["card"] = card
    g["kb"] = kb
    await _edit(bot, g, card(), kb)
    try:
        await asyncio.wait_for(g["event"].wait(), 25)
    except asyncio.TimeoutError:
        pass
    g["phase"] = "reveal"
    winners = [u for u in g["players"] if g["picks"].get(u) == correct]
    rows = []
    for u in g["players"]:
        p = g["picks"].get(u)
        rows.append(f"{WIRES[p] if p is not None else '💤'} {_nm(g, u)} {'✅' if u in winners else '❌'}")
    body = f"The live wire was {WIRES[correct]}\n" + "\n".join(rows)
    if not winners:
        await _finish_refund(bot, g, f"{body}\n\nNobody cut the right wire. The vault stays locked.")
        return
    await _finish(bot, g, winners, body)


def _total(hand: list[int]) -> int:
    return sum(hand)


async def play_card(bot: Bot, g: dict) -> None:
    g["hands"] = {u: [random.randint(1, 11), random.randint(1, 11)] for u in g["players"]}
    g["done"] = set()
    g["event"] = asyncio.Event()
    g["phase"] = "playing"
    gid = g["id"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("🃏 Hit", f"axg:h:{gid}", style="success"), btn("✋ Stand", f"axg:st:{gid}", style="danger")],
        [btn("👀 My hand", f"axg:m:{gid}", style="primary")],
    ])

    def card() -> str:
        return (
            f"{_head(g)}\n<blockquote>Each player holds a secret hand. Get close to 21 without going over.\n"
            f"✅ Ready: <b>{len(g['done'])}/{len(g['players'])}</b></blockquote>\n"
            "⏳ <i>45 seconds. Tap 👀 My hand to peek.</i>"
        )

    g["card"] = card
    g["kb"] = kb
    for u in g["players"]:  # an instant 21 stands by itself
        if _total(g["hands"][u]) >= 21:
            g["done"].add(u)
    if len(g["done"]) == len(g["players"]):
        g["event"].set()
    await _edit(bot, g, card(), kb)
    try:
        await asyncio.wait_for(g["event"].wait(), 45)
    except asyncio.TimeoutError:
        pass
    g["phase"] = "reveal"
    scores = {u: _total(h) for u, h in g["hands"].items()}
    valid = {u: s for u, s in scores.items() if s <= 21}
    rows = []
    for u in sorted(scores, key=lambda x: (scores[x] > 21, -scores[x])):
        cards = " + ".join(str(c) for c in g["hands"][u])
        rows.append(f"{'💥' if scores[u] > 21 else '🃏'} <b>{scores[u]}</b> ({cards})  {_nm(g, u)}")
    body = "\n".join(rows)
    if not valid:
        await _finish_refund(bot, g, f"{body}\n\nEveryone went over 21.")
        return
    best = max(valid.values())
    await _finish(bot, g, [u for u, s in valid.items() if s == best], body)


async def play_bomb(bot: Bot, g: dict) -> None:
    alive = list(g["players"])
    gid = g["id"]
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("💣 Pass it!", f"axg:p:{gid}", style="danger")]])
    g["phase"] = "bomb"
    out: list[int] = []

    def board(extra: str) -> str:
        rows = [f"{'🟢' if u in alive else '☠️'} {_nm(g, u)}" for u in g["players"]]
        return f"{_head(g)}\n<blockquote>" + "\n".join(rows) + f"</blockquote>\n{extra}"

    while len(alive) > 1:
        holder = random.choice(alive)
        fuse = random.randint(2, 4)
        passes = 0
        while True:
            g["holder"] = holder
            g["event"] = asyncio.Event()
            await _edit(bot, g, board(f"💣 <b>{_nm(g, holder)}</b> has the bomb! Pass it in 15 seconds!"), kb)
            try:
                await asyncio.wait_for(g["event"].wait(), 15)
            except asyncio.TimeoutError:
                victim, why = holder, "fell asleep holding the bomb"
                break
            passes += 1
            nxt = random.choice([u for u in alive if u != holder])
            if passes >= fuse:
                victim, why = nxt, "caught the bomb as it blew up"
                break
            holder = nxt
        alive.remove(victim)
        out.append(victim)
        g["holder"] = None
        await _edit(bot, g, board(f"💥 <b>{_nm(g, victim)}</b> {why}!"))
        await asyncio.sleep(2.0)
    body = "\n".join(f"☠️ {_nm(g, u)}" for u in out) + f"\n🟢 <b>{_nm(g, alive[0])}</b> survived"
    await _finish(bot, g, [alive[0]], body)


ENGINES = {
    "dice": play_dice, "bomb": play_bomb, "flip": play_flip,
    "card": play_card, "roulette": play_roulette, "hack": play_hack,
}


# --------------------------------------------------------------------------- lifecycle
def _drop(g: dict) -> None:
    key = (g["bot_id"], g["chat_id"])
    if RUN.get(key) is g:
        RUN.pop(key, None)


async def _run(bot: Bot, g: dict) -> None:
    try:
        await ENGINES[g["kind"]](bot, g)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("arcade game %s crashed, refunding", g["id"])
        try:
            await _finish_refund(bot, g, "⚠️ Something went wrong with this match.")
        except Exception:
            log.exception("arcade refund after crash failed")
    finally:
        _drop(g)


async def _lobby_timeout(bot: Bot, g: dict) -> None:
    try:
        await asyncio.sleep(LOBBY_SECONDS)
    except asyncio.CancelledError:
        return
    async with g["lock"]:
        if g["phase"] != "lobby":
            return
        g["phase"] = "closed"
    try:
        await _finish_refund(bot, g, "⌛ The lobby expired.")
    finally:
        _drop(g)


async def _start(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext, kind: str) -> None:
    user = message.from_user
    if user is None:
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("🎮 These games are played in groups. Add me to one!")
        return
    bid = bot_ctx.bot_id
    key = (bid, message.chat.id)
    if key in CLOSED:
        await message.reply("🔒 Games are closed in this group.")
        return
    emo, title, _ = GAMES[kind]
    args = cmd_args(message)
    raw = args[0].replace(",", "") if args else ""
    if not raw.isdigit():
        await message.reply(
            f"{emo} <b>{title}</b>\n<blockquote>Use <code>/{kind} 500</code>\n"
            f"Bet {_m(MIN_BET)} to {_m(MAX_BET)}. Needs 2 or more players.</blockquote>", parse_mode=H,
        )
        return
    wager = int(raw)
    if not MIN_BET <= wager <= MAX_BET:
        await message.reply(f"💰 The bet must be between <code>{_m(MIN_BET)}</code> and <code>{_m(MAX_BET)}</code>.", parse_mode=H)
        return
    if key in RUN:
        await message.reply("⏳ A game is already running in this chat. Wait for it to finish.")
        return
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    g = {
        "id": uuid.uuid4().hex[:8], "kind": kind, "bot_id": bid, "chat_id": message.chat.id,
        "host": user.id, "wager": wager, "players": [user.id], "sides": {user.id: "H"},
        "names": {user.id: user.first_name or user.username or "Player"},
        "phase": "lobby", "pool": pool, "lock": asyncio.Lock(), "message_id": 0,
    }
    RUN[key] = g  # reserve the chat before any await below, so two /dice can't race
    st = await _stake(pool, g, user.id)
    if st != "ok":
        _drop(g)
        await message.reply("💔 You don't have enough cash for that bet." if st == "poor" else "Send /start first 💜")
        return
    try:
        sent = await message.reply(_lobby_text(g), parse_mode=H, reply_markup=_lobby_kb(g))
    except TelegramAPIError:
        await _refund(pool, g)
        _drop(g)
        return
    g["message_id"] = sent.message_id
    g["timeout"] = asyncio.create_task(_lobby_timeout(bot, g))


def _make_cmd(kind: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
        await _start(message, bot, pool, bot_ctx, kind)
    return cmd


for _kind in GAMES:
    router.message(Command(_kind))(_make_cmd(_kind))


@router.callback_query(F.data.startswith("axg:"))
async def arcade_cb(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not query.message or not query.from_user:
        await query.answer()
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer()
        return
    act, gid = parts[1], parts[2]
    uid = query.from_user.id
    g = RUN.get((bot_ctx.bot_id, query.message.chat.id))
    if g is None or g["id"] != gid:
        await query.answer("This game has ended.", show_alert=True)
        return

    # ---- lobby actions
    if act in ("j", "jh", "jt"):
        async with g["lock"]:
            if g["phase"] != "lobby":
                await query.answer("The game already started.", show_alert=True)
                return
            side = "T" if act == "jt" else "H"
            if uid in g["players"]:
                if g["kind"] == "flip" and g["sides"].get(uid) != side:
                    g["sides"][uid] = side
                    await query.answer("Switched ✅")
                    await _edit(bot, g, _lobby_text(g), _lobby_kb(g))
                else:
                    await query.answer("You are already in.")
                return
            if len(g["players"]) >= MAX_PLAYERS.get(g["kind"], DEFAULT_MAX):
                await query.answer("The lobby is full.", show_alert=True)
                return
            await services.ensure_user_and_wallet(
                pool, uid, query.from_user.username, query.from_user.first_name, bot_id=g["bot_id"]
            )
            st = await _stake(pool, g, uid)
            if st != "ok":
                await query.answer(
                    f"You need {_m(g['wager'])} to join." if st == "poor" else "Send /start to me first.",
                    show_alert=True,
                )
                return
            g["players"].append(uid)
            g["sides"][uid] = side
            g["names"][uid] = query.from_user.first_name or query.from_user.username or "Player"
            await query.answer("You are in ✅")
            await _edit(bot, g, _lobby_text(g), _lobby_kb(g))
        return

    if act == "x":
        if uid != g["host"]:
            await query.answer("Only the host can cancel.", show_alert=True)
            return
        async with g["lock"]:
            if g["phase"] != "lobby":
                await query.answer("The game already started.", show_alert=True)
                return
            g["phase"] = "closed"
        g["timeout"].cancel()
        await query.answer("Cancelled")
        try:
            await _finish_refund(bot, g, "✖ The host cancelled the game.")
        finally:
            _drop(g)
        return

    if act == "s":
        if uid != g["host"]:
            await query.answer("Only the host can start.", show_alert=True)
            return
        async with g["lock"]:
            if g["phase"] != "lobby":
                await query.answer("Already started.", show_alert=True)
                return
            if len(g["players"]) < 2:
                await query.answer("Need 2 or more players!", show_alert=True)
                return
            if g["kind"] == "flip" and len(set(g["sides"][u] for u in g["players"])) < 2:
                await query.answer("Need at least one Heads and one Tails!", show_alert=True)
                return
            g["phase"] = "playing"
        g["timeout"].cancel()
        await query.answer("Good luck! 🍀")
        g["task"] = asyncio.create_task(_run(bot, g))
        return

    # ---- in-game actions (players only)
    if uid not in g["players"]:
        await query.answer("You are not in this game.", show_alert=True)
        return

    if act == "w" and g["phase"] == "picking" and len(parts) == 4 and parts[3].isdigit():
        if uid in g["picks"]:
            await query.answer("You already picked.", show_alert=True)
            return
        g["picks"][uid] = int(parts[3])
        await query.answer(f"You cut {WIRES[int(parts[3])]}")
        if len(g["picks"]) == len(g["players"]):
            g["event"].set()
        else:
            await _edit(bot, g, g["card"](), g["kb"])
        return

    if act in ("h", "st", "m") and g["phase"] == "playing" and g["kind"] == "card":
        hand = g["hands"][uid]
        if act == "m":
            await query.answer(f"Your hand: {' + '.join(map(str, hand))} = {_total(hand)}", show_alert=True)
            return
        if uid in g["done"]:
            await query.answer("You are done. Wait for the others.", show_alert=True)
            return
        if act == "st":
            g["done"].add(uid)
            await query.answer(f"You stand on {_total(hand)}")
        else:
            hand.append(random.randint(1, 11))
            total = _total(hand)
            if total > 21:
                g["done"].add(uid)
                await query.answer(f"💥 Bust! {' + '.join(map(str, hand))} = {total}", show_alert=True)
            elif total == 21:
                g["done"].add(uid)
                await query.answer(f"🎯 21! {' + '.join(map(str, hand))}", show_alert=True)
            else:
                await query.answer(f"You drew {hand[-1]}. Total {total}", show_alert=True)
        if len(g["done"]) == len(g["players"]):
            g["event"].set()
        else:
            await _edit(bot, g, g["card"](), g["kb"])
        return

    if act == "p" and g["phase"] == "bomb":
        if g.get("holder") != uid:
            await query.answer("You don't have the bomb!", show_alert=True)
            return
        ev = g.get("event")
        if ev is not None and not ev.is_set():
            ev.set()
            await query.answer("Passed! 💨")
        else:
            await query.answer()
        return

    await query.answer()


# --------------------------------------------------------------------------- admin switches
@router.message(Command("close"))
async def close_games(message: Message, bot: Bot, bot_ctx: BotContext) -> None:
    if message.from_user and message.chat.type != "private" and await ax_common.is_admin(bot, message.chat.id, message.from_user.id):
        CLOSED.add((bot_ctx.bot_id, message.chat.id))
        await message.reply("🔒 Games are closed in this group.")


@router.message(Command("open"))
async def open_games(message: Message, bot: Bot, bot_ctx: BotContext) -> None:
    if message.from_user and message.chat.type != "private" and await ax_common.is_admin(bot, message.chat.id, message.from_user.id):
        CLOSED.discard((bot_ctx.bot_id, message.chat.id))
        await message.reply("🟢 Games are open again.")


# --------------------------------------------------------------------------- startup
async def on_startup(bot: Bot, dispatcher) -> None:
    """If the bot restarted in the middle of a game, give every bet back."""
    pool = dispatcher["pool"]
    await _ensure(pool)
    async with pool.acquire() as c:
        async with c.transaction():
            rows = await c.fetch(
                "SELECT bot_id, game_id, chat_id, user_id, amount, kind FROM ax_arcade_escrow FOR UPDATE"
            )
            await _refund_rows(c, rows)
    chats = {(r["chat_id"]) for r in rows}
    if rows:
        log.info("Refunded %s unfinished arcade bet(s).", len(rows))
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, "🔄 I restarted, so the unfinished game was cancelled and every bet was refunded.")
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

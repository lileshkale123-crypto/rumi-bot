"""Random group events: a Catch drop (/catch name) and a Number Guess drop (/g 250).

Rules: a group needs EVENT_MIN_GROUP_MEMBERS members, gets at most EVENT_MAX_PER_GROUP_PER_DAY events a day
(IST), and an event only drops while people are chatting, at a random moment.
Guess image: put it at assets/guess_event.jpg (or .png), or set GUESS_EVENT_IMAGE to a Telegram file_id / URL.
Owners can test with /spawnevent catch or /spawnevent guess in a group.
"""
import asyncio
import logging
import os
import random
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import aiohttp
import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import bot_context
import config
import services
from ax_common import cmd_args, esc
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_eventspawn")
H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))

MIN_MEMBERS = int(getattr(config, "EVENT_MIN_GROUP_MEMBERS", 25))
MAX_PER_DAY = int(getattr(config, "EVENT_MAX_PER_GROUP_PER_DAY", 1))
CATCH_REWARD = int(getattr(config, "CATCH_REWARD_COINS", 2000))
GUESS_REWARD = (int(getattr(config, "EVENT_REWARD_MIN", 500)), int(getattr(config, "EVENT_REWARD_MAX", 1000)))
GUESS_RANGE = (int(getattr(config, "GUESS_MIN_NUMBER", 100)), int(getattr(config, "GUESS_MAX_NUMBER", 500)))
GUESS_TRIES = int(getattr(config, "GUESS_MAX_TRIES", 12))
EVENT_LIFETIME = 24 * 60 * 60     # an event stays until someone wins it (or 24 hours pass)
ACTIVE_WINDOW = 10 * 60           # chat must have talked this recently
MIN_RECENT_MSGS = 6               # ...and sent this many messages in that window
CHANCE_PER_MIN = 0.06             # per minute of activity (random drop time)

FALLBACK_CHARACTERS = [
    "Naruto Uzumaki", "Sasuke Uchiha", "Monkey D. Luffy", "Roronoa Zoro", "Son Goku", "Vegeta",
    "Light Yagami", "Levi Ackerman", "Eren Yeager", "Mikasa Ackerman", "Tanjiro Kamado", "Nezuko Kamado",
    "Zenitsu Agatsuma", "Gojo Satoru", "Yuji Itadori", "Megumi Fushiguro", "Izuku Midoriya", "Katsuki Bakugo",
    "Edward Elric", "Alphonse Elric", "Lelouch Lamperouge", "Spike Spiegel", "Killua Zoldyck", "Gon Freecss",
    "Hinata Hyuga", "Itachi Uchiha", "Kakashi Hatake", "Rem", "Asuna Yuuki", "Kirito", "Saitama", "Genos",
    "Anya Forger", "Loid Forger", "Yor Forger", "Makima", "Denji", "Power", "Zero Two", "Misa Amane",
]

# (bot_id, chat_id) -> live event
ACTIVE: dict[tuple[int, int], dict] = {}
# (bot_id, chat_id) -> activity info
SEEN: dict[tuple[int, int], dict] = {}
_TODAY: dict[tuple[int, int, str], int] = {}
_MEMBERS: dict[tuple[int, int], tuple[float, int]] = {}
_LOOP: asyncio.Task | None = None
_IMG: dict[int, str] = {}      # bot_id -> guess image (file_id or URL)
_POOL: list = []
IMG_ADMINS = {8920362130}     # may use /setguessimg besides the bot owners
_TABLE_READY = False
_MW = False


def _day() -> str:
    return datetime.now(IST).date().isoformat()


async def _ensure(pool: asyncpg.Pool) -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_event_log ("
            "id BIGSERIAL PRIMARY KEY, bot_id BIGINT NOT NULL DEFAULT 0, chat_id BIGINT NOT NULL, "
            "day_key DATE NOT NULL, kind TEXT NOT NULL, winner_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    _TABLE_READY = True


# ---------------------------------------------------------------- payouts
async def _pay(pool: asyncpg.Pool, bid: int, user, amount: int, kind: str, tag: str) -> bool:
    try:
        await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
        async with pool.acquire() as c:
            async with c.transaction():
                w = await c.fetchrow("SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, user.id)
                if w is None:
                    return False
                await c.execute(
                    "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                    amount, bid, user.id,
                )
                await services.insert_transaction(
                    c, user_id=user.id, type_="EVENT_REWARD", currency="cash", amount=amount,
                    balance_before=int(w["cash"]), balance_after=int(w["cash"]) + amount, source=kind,
                    metadata={"event": tag}, idempotency_key=f"event:{bid}:{tag}", bot_id=bid,
                )
        return True
    except Exception:
        log.exception("event payout failed")
        return False


# ---------------------------------------------------------------- catch characters
def _clean_name(raw: str) -> str:
    raw = re.sub(r"\s+", " ", (raw or "").strip())
    if "," in raw:  # Jikan gives "Uzumaki, Naruto"
        last, first = [p.strip() for p in raw.split(",", 1)]
        raw = f"{first} {last}".strip()
    return raw


async def _pick_character() -> tuple[str, str | None]:
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            for _ in range(3):
                async with s.get("https://api.jikan.moe/v4/random/characters") as r:
                    if r.status != 200:
                        continue
                    d = (await r.json()).get("data") or {}
                name = _clean_name(d.get("name") or "")
                img = ((d.get("images") or {}).get("jpg") or {}).get("image_url")
                if name and img and "questionmark" not in img and len(name) <= 40 and name.isascii():
                    return name, img
    except Exception:
        pass
    return random.choice(FALLBACK_CHARACTERS), None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("-", " ")).strip()


def _name_matches(guess: str, name: str) -> bool:
    g, n = _norm(guess), _norm(name)
    if not g:
        return False
    if g == n:
        return True
    return len(g) >= 3 and g in n.split()


def _mask(name: str) -> str:
    return "  ".join(w[0] + " " + " ".join("_" * (len(w) - 1)) if len(w) > 1 else w for w in name.split())


# ---------------------------------------------------------------- spawning
def _guess_image(bid: int):
    if bid in _IMG:
        return _IMG[bid]
    env = os.getenv("GUESS_EVENT_IMAGE") or getattr(config, "GUESS_EVENT_IMAGE", "")
    if env:
        return env
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = os.path.join(HERE, "assets", f"guess_event.{ext}")
        if os.path.exists(p):
            from aiogram.types import FSInputFile
            return FSInputFile(p)
    return None


async def _ensure_settings(pool) -> None:
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS ax_event_settings (bot_id BIGINT NOT NULL, key TEXT NOT NULL, "
                        "value TEXT NOT NULL, PRIMARY KEY (bot_id, key))")


async def _load_img(bid: int) -> None:
    if bid in _IMG or not _POOL:
        return
    try:
        await _ensure_settings(_POOL[0])
        async with _POOL[0].acquire() as c:
            v = await c.fetchval("SELECT value FROM ax_event_settings WHERE bot_id=$1 AND key='guess_image'", bid)
        if v:
            _IMG[bid] = v
    except Exception:
        log.exception("could not load the guess image")


async def _send(bot: Bot, chat_id: int, text: str, photo=None) -> Message | None:
    if photo is not None:
        try:
            return await bot.send_photo(chat_id, photo, caption=text, parse_mode=H)
        except Exception:
            log.warning("event photo failed, sending text instead")
    try:
        return await bot.send_message(chat_id, text, parse_mode=H)
    except Exception:
        log.exception("event message failed")
        return None


async def spawn(bot: Bot, bid: int, chat_id: int, kind: str) -> bool:
    key = (bid, chat_id)
    if key in ACTIVE:
        return False
    now = time.time()
    if kind == "catch":
        name, img = await _pick_character()
        if key in ACTIVE:
            return False
        ev = {"kind": "catch", "name": name, "ends": now + EVENT_LIFETIME, "tag": f"catch:{chat_id}:{int(now)}"}
        text = (
            "🎴 <b>A WILD CHARACTER APPEARED!</b>\n"
            f"<blockquote>Who is this? <code>{esc(_mask(name))}</code>\n"
            f"Be the first to type <code>/catch name</code> and win <b>${CATCH_REWARD:,}</b>!</blockquote>\n"
            "<i>It stays until someone catches it (24 hours at most).</i>"
        )
        ACTIVE[key] = ev
        sent = await _send(bot, chat_id, text, img)
    else:
        secret = random.randint(*GUESS_RANGE)
        reward = random.randint(*GUESS_REWARD)
        ev = {"kind": "guess", "secret": secret, "reward": reward, "lo": GUESS_RANGE[0], "hi": GUESS_RANGE[1],
              "tries": 0, "ends": now + EVENT_LIFETIME, "tag": f"guess:{chat_id}:{int(now)}"}
        text = (
            "🎯 <b>NUMBER GUESS EVENT!</b>\n"
            f"<blockquote>I'm thinking of a number from <b>{GUESS_RANGE[0]}</b> to <b>{GUESS_RANGE[1]}</b>.\n"
            f"Type <code>/g 250</code> to guess. First to get it wins <b>${reward:,}</b>!\n"
            f"The whole group shares <b>{GUESS_TRIES}</b> tries.</blockquote>\n"
            "<i>It stays open until someone wins (24 hours at most).</i>"
        )
        ACTIVE[key] = ev
        await _load_img(bid)
        sent = await _send(bot, chat_id, text, _guess_image(bid))
        if sent is not None and sent.photo and bid not in _IMG:
            _IMG[bid] = sent.photo[-1].file_id
    if sent is None:
        ACTIVE.pop(key, None)
        return False
    ev["bot"] = bot
    return True


async def _expire(bid: int, chat_id: int, ev: dict) -> None:
    if ACTIVE.get((bid, chat_id)) is not ev:
        return
    ACTIVE.pop((bid, chat_id), None)
    if ev["kind"] == "catch":
        txt = f"💨 The character ran away. It was <b>{esc(ev['name'])}</b>."
    else:
        txt = f"⌛ Time is up! The number was <b>{ev['secret']}</b>."
    try:
        await ev["bot"].send_message(chat_id, txt, parse_mode=H)
    except Exception:
        pass


async def _members(bot: Bot, bid: int, chat_id: int) -> int:
    hit = _MEMBERS.get((bid, chat_id))
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    try:
        n = int(await bot.get_chat_member_count(chat_id))
    except Exception:
        return 0
    _MEMBERS[(bid, chat_id)] = (time.time(), n)
    return n


async def _tick() -> None:
    now = time.time()
    for key, ev in list(ACTIVE.items()):
        if now > ev["ends"]:
            await _expire(key[0], key[1], ev)
    for key, info in list(SEEN.items()):
        bid, chat_id = key
        recent = [t for t in info["msgs"] if now - t < ACTIVE_WINDOW]
        if not recent:
            SEEN.pop(key, None)
            continue
        if key in ACTIVE or len(recent) < MIN_RECENT_MSGS or random.random() > CHANCE_PER_MIN:
            continue
        day = _day()
        pool = info["pool"]
        try:
            await _ensure(pool)
            done = _TODAY.get((bid, chat_id, day))
            if done is None:
                async with pool.acquire() as c:
                    done = int(await c.fetchval(
                        "SELECT COUNT(*) FROM ax_event_log WHERE bot_id=$1 AND chat_id=$2 AND day_key=$3::date",
                        bid, chat_id, day))
                _TODAY[(bid, chat_id, day)] = done
            if done >= MAX_PER_DAY:
                continue
            if await _members(info["bot"], bid, chat_id) < MIN_MEMBERS:
                continue
            kind = random.choice(("catch", "guess"))
            _TODAY[(bid, chat_id, day)] = done + 1          # claim first, so no double drop
            if await spawn(info["bot"], bid, chat_id, kind):
                async with pool.acquire() as c:
                    await c.execute(
                        "INSERT INTO ax_event_log (bot_id, chat_id, day_key, kind) VALUES ($1,$2,$3::date,$4)",
                        bid, chat_id, day, kind)
            else:
                _TODAY[(bid, chat_id, day)] = done
        except Exception:
            log.exception("event tick failed for chat %s", chat_id)


async def _loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await _tick()
        except Exception:
            log.exception("event loop error")


class ActivityMiddleware(BaseMiddleware):
    """Remembers which groups are chatting right now (nothing is stored in the database)."""

    async def __call__(self, handler, event, data):
        global _LOOP
        try:
            if isinstance(event, Message) and event.chat.type in ("group", "supergroup") and event.from_user \
                    and not event.from_user.is_bot:
                pool = data.get("pool")
                bid = data.get("bot_id") or (data.get("bot_ctx").bot_id if data.get("bot_ctx") else event.bot.id)
                if pool is not None:
                    _POOL[:] = [pool]
                    info = SEEN.setdefault((bid, event.chat.id), {"msgs": deque(maxlen=40)})
                    info.update(pool=pool, bot=event.bot)
                    info["msgs"].append(time.time())
                    if _LOOP is None or _LOOP.done():
                        _LOOP = asyncio.get_running_loop().create_task(_loop())
        except Exception:
            log.exception("event activity watcher failed")
        return await handler(event, data)


# ---------------------------------------------------------------- commands
def _in_group(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup")


@router.message(Command("catch"))
async def catch_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user or not _in_group(message):
        return
    key = (bot_ctx.bot_id, message.chat.id)
    ev = ACTIVE.get(key)
    guess = " ".join(cmd_args(message)).strip()
    if not ev or ev["kind"] != "catch":
        await message.reply("Nothing to catch right now. Wait for a character to appear!")
        return
    if not guess:
        await message.reply("Use: <code>/catch character name</code>", parse_mode=H)
        return
    if not _name_matches(guess, ev["name"]):
        await message.reply("❌ Not them. Try again!")
        return
    if ACTIVE.pop(key, None) is None:
        return
    ok = await _pay(pool, bot_ctx.bot_id, message.from_user, CATCH_REWARD, "catch", ev["tag"])
    who = esc(message.from_user.first_name or "Player")
    if ok:
        await message.reply(f"🎉 <b>{who}</b> caught <b>{esc(ev['name'])}</b> and won <code>+${CATCH_REWARD:,}</code>!", parse_mode=H)
    else:
        await message.reply(f"🎉 <b>{who}</b> caught <b>{esc(ev['name'])}</b>, but the prize could not be paid. Please tell an admin.", parse_mode=H)
    await _log_winner(pool, bot_ctx.bot_id, message.chat.id, "catch", message.from_user.id)


async def _log_winner(pool, bid, chat_id, kind, uid) -> None:
    try:
        async with pool.acquire() as c:
            await c.execute(
                "UPDATE ax_event_log SET winner_id=$4 WHERE id=(SELECT id FROM ax_event_log "
                "WHERE bot_id=$1 AND chat_id=$2 AND kind=$3 AND winner_id IS NULL ORDER BY id DESC LIMIT 1)",
                bid, chat_id, kind, uid)
    except Exception:
        pass


@router.message(Command("g"))
async def guess_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user or not _in_group(message):
        return
    key = (bot_ctx.bot_id, message.chat.id)
    ev = ACTIVE.get(key)
    if not ev or ev["kind"] != "guess":
        await message.reply("No number guess is running right now.")
        return
    args = cmd_args(message)
    lo_all, hi_all = GUESS_RANGE
    if not args or not args[0].isdigit() or not (lo_all <= int(args[0]) <= hi_all):
        await message.reply(f"Use: <code>/g 250</code> (a number from {lo_all} to {hi_all})", parse_mode=H)
        return
    val = int(args[0])
    if val == ev["secret"]:
        if ACTIVE.pop(key, None) is None:
            return
        ok = await _pay(pool, bot_ctx.bot_id, message.from_user, ev["reward"], "guess", ev["tag"])
        who = esc(message.from_user.first_name or "Player")
        tail = f"won <code>+${ev['reward']:,}</code>!" if ok else "won, but the prize could not be paid. Please tell an admin."
        await message.reply(f"🎯 Correct! <b>{who}</b> guessed <code>{val}</code> and {tail}", parse_mode=H)
        await _log_winner(pool, bot_ctx.bot_id, message.chat.id, "guess", message.from_user.id)
        return
    ev["tries"] += 1
    if val < ev["secret"]:
        ev["lo"] = max(ev["lo"], val + 1)
        hint = "⬆️ Higher!"
    else:
        ev["hi"] = min(ev["hi"], val - 1)
        hint = "⬇️ Lower!"
    left = GUESS_TRIES - ev["tries"]
    if left <= 0:
        ACTIVE.pop(key, None)
        await message.reply(f"{hint}\n💥 No tries left! The number was <b>{ev['secret']}</b>.", parse_mode=H)
        return
    await message.reply(f"{hint} It is between <code>{ev['lo']}</code> and <code>{ev['hi']}</code>. Tries left: <b>{left}</b>", parse_mode=H)


@router.message(Command("spawnevent"))
async def spawn_cmd(message: Message, bot: Bot, bot_ctx: BotContext) -> None:
    if not message.from_user or not _can_manage(message.from_user.id) or not _in_group(message):
        return
    args = cmd_args(message)
    kind = args[0].lower() if args and args[0].lower() in ("catch", "guess") else random.choice(("catch", "guess"))
    ok = await spawn(bot, bot_ctx.bot_id, message.chat.id, kind)
    if not ok:
        await message.reply("An event is already running here (or it could not be sent).")


def _can_manage(uid: int) -> bool:
    if uid in IMG_ADMINS:
        return True
    try:
        from ax_adminpanel import OWNER_IDS
        return uid in OWNER_IDS
    except Exception:
        return False


@router.message(Command("setguessimg"))
async def setimg_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    """Reply to a photo with /setguessimg to use it for the guess event. /setguessimg clear removes it."""
    u = message.from_user
    if not u or not _can_manage(u.id):
        return
    _POOL[:] = [pool]
    bid = bot_ctx.bot_id
    await _ensure_settings(pool)
    args = cmd_args(message)
    if args and args[0].lower() == "clear":
        async with pool.acquire() as c:
            await c.execute("DELETE FROM ax_event_settings WHERE bot_id=$1 AND key='guess_image'", bid)
        _IMG.pop(bid, None)
        await message.reply("🗑 Guess event image removed.")
        return
    rep = message.reply_to_message
    if not rep or not rep.photo:
        await message.reply("Send the image here, then reply to it with <code>/setguessimg</code>.", parse_mode=H)
        return
    fid = rep.photo[-1].file_id
    async with pool.acquire() as c:
        await c.execute("INSERT INTO ax_event_settings (bot_id, key, value) VALUES ($1,'guess_image',$2) "
                        "ON CONFLICT (bot_id, key) DO UPDATE SET value=EXCLUDED.value", bid, fid)
    _IMG[bid] = fid
    await message.reply("✅ Saved! The guess event will use this image.\nCode: <code>" + esc(fid) + "</code>", parse_mode=H)


def register(dp) -> None:
    global _MW
    if not _MW:
        _MW = True
        dp.message.outer_middleware(ActivityMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

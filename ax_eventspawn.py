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
from datetime import date, datetime, timedelta, timezone

import aiohttp
import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
_CTX: dict[int, dict] = {}        # bot_id -> {'bot':..., 'pool':...}
_LAST: dict[tuple[int, int], float] = {}   # (bot, chat) -> when the last event dropped
_SKIP: set = set()
_MSGS_READY = False

FALLBACK_CHARACTERS = [
    "Naruto Uzumaki", "Sasuke Uchiha", "Monkey D. Luffy", "Roronoa Zoro", "Son Goku", "Vegeta",
    "Light Yagami", "Levi Ackerman", "Eren Yeager", "Mikasa Ackerman", "Tanjiro Kamado", "Nezuko Kamado",
    "Zenitsu Agatsuma", "Gojo Satoru", "Yuji Itadori", "Megumi Fushiguro", "Izuku Midoriya", "Katsuki Bakugo",
    "Edward Elric", "Alphonse Elric", "Lelouch Lamperouge", "Spike Spiegel", "Killua Zoldyck", "Gon Freecss",
    "Hinata Hyuga", "Itachi Uchiha", "Kakashi Hatake", "Rem", "Asuna Yuuki", "Kirito", "Saitama", "Genos",
    "Anya Forger", "Loid Forger", "Yor Forger", "Makima", "Denji", "Power", "Zero Two", "Misa Amane",
]

# LUNASPAWN_V1  Luna-style catch drop: same pool, same texts, nekos.best wink GIF
CHARACTER_POOL = [
    {"name": "Raiden", "title": "Raiden Shogun", "anime": "Genshin Impact", "rarity": "🌟 Mythic", "reward": 2500},
    {"name": "Gojo", "title": "Satoru Gojo", "anime": "Jujutsu Kaisen", "rarity": "🔮 Celestial", "reward": 3000},
    {"name": "Makima", "title": "Makima", "anime": "Chainsaw Man", "rarity": "🔥 Legendary", "reward": 2200},
    {"name": "Mikasa", "title": "Mikasa Ackerman", "anime": "Attack on Titan", "rarity": "💎 Epic", "reward": 1800},
    {"name": "ZeroTwo", "title": "Zero Two", "anime": "Darling in the Franxx", "rarity": "🌸 Mythic", "reward": 2600},
    {"name": "Levi", "title": "Levi Ackerman", "anime": "Attack on Titan", "rarity": "⚔️ Legendary", "reward": 2400},
    {"name": "Yor", "title": "Yor Forger", "anime": "Spy x Family", "rarity": "🌹 Epic", "reward": 1900},
    {"name": "Sukuna", "title": "Ryomen Sukuna", "anime": "Jujutsu Kaisen", "rarity": "🩸 Celestial", "reward": 3200},
]
SYM = getattr(config, "CURRENCY_SYMBOL", "$")
WINK_FALLBACK = "https://media.giphy.com/media/l4pTdcifPZLpDjL1e/giphy.gif"
_HAREM_READY = False


async def _wink_gif() -> str:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get("https://nekos.best/api/v2/wink") as r:
                if r.status == 200:
                    return (await r.json())["results"][0]["url"]
    except Exception:
        pass
    return WINK_FALLBACK


def _char_matches(guess: str, char: dict) -> bool:
    g = _norm(guess)
    if not g:
        return False
    if g.replace(" ", "") == _norm(char["name"]).replace(" ", ""):
        return True
    return _name_matches(guess, char["title"])


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
        char = random.choice(CHARACTER_POOL)
        gif = await _wink_gif()
        if key in ACTIVE:
            return False
        ev = {"kind": "catch", "name": char["title"], "char": char, "ends": now + EVENT_LIFETIME,
              "tag": f"catch:{chat_id}:{int(now)}"}
        text = (
            f"✨ <b>A WILD CHARACTER APPEARED!</b>\n\n"
            f"<blockquote>🎭 <b>Series:</b> {char['anime']}\n"
            f"🌟 <b>Rarity:</b> <code>{char['rarity']}</code>\n"
            f"💰 <b>Bounty:</b> <code>+{SYM}{char['reward']:,}</code></blockquote>\n"
            f"⚡ Type <code>/catch &lt;name&gt;</code> first to claim them! <i>(Hint: starts with <b>{char['name'][0]}</b>)</i>"
        )
        ACTIVE[key] = ev
        sent = None
        try:
            sent = await bot.send_animation(chat_id, animation=gif, caption=text, parse_mode=H)
        except Exception:
            log.warning("catch gif failed, sending text instead")
            sent = await _send(bot, chat_id, text)
    else:
        secret = random.randint(*GUESS_RANGE)
        reward = random.randint(*GUESS_REWARD)
        ev = {"kind": "guess", "secret": secret, "reward": reward, "lo": GUESS_RANGE[0], "hi": GUESS_RANGE[1],
              "tries": 0, "ends": now + EVENT_LIFETIME, "tag": f"guess:{chat_id}:{int(now)}"}
        text = (
            "🎯 <b>NUMBER GUESS EVENT!</b>\n"
            f"<blockquote>I'm thinking of a number from <b>{GUESS_RANGE[0]}</b> to <b>{GUESS_RANGE[1]}</b>.\n"
            f"Type <code>/g 250</code> to guess. First to get it wins <b>${reward:,}</b>!\n"
            f"The whole group shares <b>{GUESS_TRIES}</b> tries.</blockquote>"
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
    _LAST[key] = now
    await _track(bid, chat_id, [sent.message_id], now)
    return True


async def _expire(bid: int, chat_id: int, ev: dict) -> None:
    if ACTIVE.get((bid, chat_id)) is ev:
        ACTIVE.pop((bid, chat_id), None)   # silent: the messages are removed by the cleaner


async def _ensure_msgs(pool) -> None:
    global _MSGS_READY
    if _MSGS_READY:
        return
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS ax_event_msgs (bot_id BIGINT NOT NULL, chat_id BIGINT NOT NULL, "
                        "message_id BIGINT NOT NULL, delete_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (bot_id, chat_id, message_id))")
    _MSGS_READY = True


async def _track(bid: int, chat_id: int, ids: list, born: float) -> None:
    ctx = _CTX.get(bid)
    if not ctx:
        return
    try:
        await _ensure_msgs(ctx["pool"])
        async with ctx["pool"].acquire() as c:
            for i in ids:
                await c.execute("INSERT INTO ax_event_msgs (bot_id, chat_id, message_id, delete_at) VALUES ($1,$2,$3,$4) "
                                "ON CONFLICT DO NOTHING", bid, chat_id, int(i), born + EVENT_LIFETIME)
    except Exception:
        log.exception("event message tracking failed")


async def _r(message: Message, text: str, **kw):
    """reply, and remember both the user's message and our reply for the 1-day cleanup"""
    bid = bot_context.resolve_bot_id(None)
    born = _LAST.get((bid, message.chat.id))
    sent = await message.reply(text, **kw)
    if born and time.time() < born + EVENT_LIFETIME:
        await _track(bid, message.chat.id, [message.message_id, sent.message_id], born)
    return sent


async def _clean(bid: int, ctx: dict) -> None:
    try:
        await _ensure_msgs(ctx["pool"])
        async with ctx["pool"].acquire() as c:
            rows = await c.fetch("SELECT chat_id, message_id FROM ax_event_msgs WHERE bot_id=$1 AND delete_at<=$2 LIMIT 80",
                                 bid, time.time())
            for r in rows:
                try:
                    await ctx["bot"].delete_message(r["chat_id"], r["message_id"])
                except Exception as exc:
                    if "retry" in type(exc).__name__.lower():
                        return
                await c.execute("DELETE FROM ax_event_msgs WHERE bot_id=$1 AND chat_id=$2 AND message_id=$3",
                                bid, r["chat_id"], r["message_id"])
                await asyncio.sleep(0.05)
    except Exception:
        log.exception("event cleanup failed")


def _drop_at(bid: int, chat_id: int, day: str) -> float:
    """a random but fixed moment of the (IST) day for this group"""
    sec = random.Random(f"{bid}:{chat_id}:{day}").randint(0, 86399)
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, tzinfo=IST).timestamp() + sec


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
    day = _day()
    for bid, ctx in list(_CTX.items()):
        await _clean(bid, ctx)
        pool, bot = ctx["pool"], ctx["bot"]
        try:
            await _ensure(pool)
            async with pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT g.chat_id FROM ax_groups g WHERE g.bot_id=$1 AND g.chat_id < 0 "
                    "AND NOT EXISTS (SELECT 1 FROM ax_channels ch WHERE ch.chat_id = g.chat_id)", bid)
        except Exception:
            log.exception("event group list failed")
            continue
        started = 0
        for row in rows:
            chat_id = row["chat_id"]
            key = (bid, chat_id)
            if key in ACTIVE or (bid, chat_id, day) in _SKIP or now < _drop_at(bid, chat_id, day) or started >= 3:
                continue
            try:
                done = _TODAY.get((bid, chat_id, day))
                if done is None:
                    async with pool.acquire() as c:
                        done = int(await c.fetchval(
                            "SELECT COUNT(*) FROM ax_event_log WHERE bot_id=$1 AND chat_id=$2 AND day_key=$3::date",
                            bid, chat_id, date.fromisoformat(day)))
                    _TODAY[(bid, chat_id, day)] = done
                if done >= MAX_PER_DAY:
                    _SKIP.add((bid, chat_id, day))
                    continue
                if await _members(bot, bid, chat_id) < MIN_MEMBERS:
                    _SKIP.add((bid, chat_id, day))
                    continue
                kind = random.choice(("catch", "guess"))
                _TODAY[(bid, chat_id, day)] = done + 1
                _SKIP.add((bid, chat_id, day))      # one try per day, never spam a group that fails
                if await spawn(bot, bid, chat_id, kind):
                    started += 1
                    async with pool.acquire() as c:
                        await c.execute(
                            "INSERT INTO ax_event_log (bot_id, chat_id, day_key, kind) VALUES ($1,$2,$3::date,$4)",
                            bid, chat_id, date.fromisoformat(day), kind)
                else:
                    _TODAY[(bid, chat_id, day)] = done
            except Exception:
                log.exception("event tick failed for chat %s", chat_id)
    if len(_SKIP) > 5000:
        _SKIP.clear()


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
                    _CTX[bid] = {"bot": event.bot, "pool": pool}
                    if _LOOP is None or _LOOP.done():
                        _LOOP = asyncio.get_running_loop().create_task(_loop())
        except Exception:
            log.exception("event activity watcher failed")
        return await handler(event, data)


# ---------------------------------------------------------------- commands
def _in_group(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup")


@router.message(Command("catch", "grab", "claimchar"))
async def catch_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user or not _in_group(message):
        return
    key = (bot_ctx.bot_id, message.chat.id)
    ev = ACTIVE.get(key)
    guess = " ".join(cmd_args(message)).strip()
    if not ev or ev["kind"] != "catch":
        await _r(message, "💨 No character is currently waiting to be caught!")
        return
    if not guess:
        await _r(message, "📌 Guess their name! Usage: <code>/catch &lt;name&gt;</code>", parse_mode=H)
        return
    char = ev.get("char") or {"name": ev["name"], "title": ev["name"], "rarity": "", "reward": CATCH_REWARD}
    if not _char_matches(guess, char):
        await _r(message, "❌ Wrong character name! Keep trying!")
        return
    if ACTIVE.pop(key, None) is None:
        return
    user = message.from_user
    bid = bot_ctx.bot_id
    text = (
        f"🎉 <b>CHARACTER CAUGHT!</b>\n\n"
        f"<blockquote>👤 {user.mention_html()} captured <b>{char['title']}</b>!\n"
        f"🌟 <b>Rarity:</b> <code>{char['rarity']}</code>\n"
        f"💰 <b>Reward:</b> <code>+{SYM}{char['reward']:,}</code></blockquote>"
    )
    ok, _ = await asyncio.gather(
        _pay(pool, bid, user, int(char["reward"]), "catch", ev["tag"]),
        _r(message, text, parse_mode=H),
        return_exceptions=True,
    )
    if ok is not True:
        try:
            await message.reply("⚠️ The prize could not be paid. Please tell an admin.")
        except Exception:
            pass
    asyncio.get_running_loop().create_task(_after_catch(pool, bid, message.chat.id, user.id, char))


async def _ensure_harem(pool) -> None:
    global _HAREM_READY
    if _HAREM_READY:
        return
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS ax_harem (id BIGSERIAL PRIMARY KEY, bot_id BIGINT NOT NULL, "
                        "user_id BIGINT NOT NULL, title TEXT NOT NULL, rarity TEXT NOT NULL DEFAULT '', "
                        "caught_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        await c.execute("CREATE INDEX IF NOT EXISTS ax_harem_user ON ax_harem (bot_id, user_id, id)")
    _HAREM_READY = True


async def _after_catch(pool, bid, chat_id, uid, char) -> None:
    try:
        await _ensure_harem(pool)
        async with pool.acquire() as c:
            await c.execute("INSERT INTO ax_harem (bot_id, user_id, title, rarity) VALUES ($1,$2,$3,$4)",
                            bid, uid, char["title"], char.get("rarity") or "")
    except Exception:
        log.exception("harem save failed")
    await _log_winner(pool, bid, chat_id, "catch", uid)


# COLLECTION_V1  /collection hub with category buttons + pages.
# To add a new collection type later: write count_fn + items_fn and add one entry to COLLECTIONS.
COL_PAGE = 8
_RARITY_ORDER = ["celestial", "mythic", "legendary", "epic", "rare", "common"]
_POOL_SIZE = len(CHARACTER_POOL)


def _rarity_rank(r: str) -> int:
    low = (r or "").lower()
    for i, w in enumerate(_RARITY_ORDER):
        if w in low:
            return i
    return len(_RARITY_ORDER)


def _bar(have: int, total: int, size: int = 10) -> str:
    if total <= 0:
        return ""
    fill = min(size, round(size * have / total))
    return "▰" * fill + "▱" * (size - fill)


async def _chars_count(pool, bid, uid) -> tuple[int, int]:
    await _ensure_harem(pool)
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT COUNT(*) AS n, COUNT(DISTINCT title) AS u FROM ax_harem "
                             "WHERE bot_id=$1 AND user_id=$2", bid, uid)
    return int(r["n"]), int(r["u"])


async def _chars_items(pool, bid, uid) -> list[str]:
    await _ensure_harem(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT title, rarity, COUNT(*) AS n FROM ax_harem WHERE bot_id=$1 AND user_id=$2 "
                             "GROUP BY title, rarity", bid, uid)
    anime = {ch["title"]: ch["anime"] for ch in CHARACTER_POOL}
    rows = sorted(rows, key=lambda r: (_rarity_rank(r["rarity"]), r["title"]))
    out = []
    for r in rows:
        rar = r["rarity"] or ""
        icon, _, word = rar.partition(" ")
        dup = f" <b>×{r['n']}</b>" if r["n"] > 1 else ""
        series = anime.get(r["title"])
        sub = f"\n     <i>{esc(word or rar)}{' · ' + esc(series) if series else ''}</i>"
        out.append(f"{esc(icon) if word else '🎴'} <b>{esc(r['title'])}</b>{dup}{sub}")
    return out


COLLECTIONS = {
    "ch": {"icon": "🎴", "label": "Characters", "total": _POOL_SIZE,
           "count": _chars_count, "items": _chars_items, "empty": "Catch one with <code>/catch</code> when a character appears!"},
}


def _col_home_kb(oid, tid, counts) -> InlineKeyboardMarkup:
    rows, line = [], []
    for key, col in COLLECTIONS.items():
        n = counts.get(key, (0, 0))[0]
        line.append(InlineKeyboardButton(text=f"{col['icon']} {col['label']} ({n})", callback_data=f"cl:{oid}:{tid}:{key}:0"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([InlineKeyboardButton(text="✖️ Close", callback_data=f"cl:{oid}:{tid}:x:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _col_home(pool, bid, oid, target) -> tuple[str, InlineKeyboardMarkup]:
    counts = {}
    res = await asyncio.gather(*(c["count"](pool, bid, target.id) for c in COLLECTIONS.values()), return_exceptions=True)
    for key, r in zip(COLLECTIONS, res):
        counts[key] = r if isinstance(r, tuple) else (0, 0)
    total = sum(v[0] for v in counts.values())
    lines = []
    for key, col in COLLECTIONS.items():
        n, u = counts[key]
        t = col.get("total") or 0
        prog = f"  {_bar(u, t, 8)} <code>{u}/{t}</code>" if t else ""
        lines.append(f"{col['icon']} <b>{col['label']}:</b> <code>{n}</code>{prog}")
    name = esc(target.first_name or "Player")
    text = (
        f"🗂 <b>{name.upper()}'S COLLECTION</b>\n\n"
        f"<blockquote>" + "\n".join(lines) + "</blockquote>\n"
        f"📦 <b>Total items:</b> <code>{total}</code>\n"
        f"👇 <i>Pick a category to open it</i>"
    )
    return text, _col_home_kb(oid, target.id, counts)


async def _col_page(pool, bid, oid, tid, tname, key, page) -> tuple[str, InlineKeyboardMarkup]:
    col = COLLECTIONS[key]
    items, (n, u) = await asyncio.gather(col["items"](pool, bid, tid), col["count"](pool, bid, tid))
    pages = max(1, (len(items) + COL_PAGE - 1) // COL_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * COL_PAGE:(page + 1) * COL_PAGE]
    t = col.get("total") or 0
    head = f"{col['icon']} <b>{esc(tname).upper()} · {col['label'].upper()}</b>\n"
    if t:
        head += f"<code>{_bar(u, t)}</code> <b>{u}/{t}</b> unlocked · <code>{n}</code> caught\n"
    if chunk:
        body = "<blockquote>" + "\n".join(chunk) + "</blockquote>"
    else:
        body = f"<blockquote>Nothing here yet. {col['empty']}</blockquote>"
    nav = []
    if pages > 1:
        nav = [
            InlineKeyboardButton(text="◀️", callback_data=f"cl:{oid}:{tid}:{key}:{(page - 1) % pages}"),
            InlineKeyboardButton(text=f"📄 {page + 1}/{pages}", callback_data=f"cl:{oid}:{tid}:n:0"),
            InlineKeyboardButton(text="▶️", callback_data=f"cl:{oid}:{tid}:{key}:{(page + 1) % pages}"),
        ]
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"cl:{oid}:{tid}:h:0"),
                 InlineKeyboardButton(text="✖️ Close", callback_data=f"cl:{oid}:{tid}:x:0")])
    return head + "\n" + body, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("collection", "col", "mycollection"))
async def collection_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    u = message.from_user
    if not u:
        return
    rep = message.reply_to_message
    target = rep.from_user if rep and rep.from_user and not rep.from_user.is_bot else u
    try:
        text, kb = await _col_home(pool, bot_ctx.bot_id, u.id, target)
    except Exception:
        log.exception("collection failed")
        await message.reply("⚠️ Could not open the collection right now. Try again.")
        return
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.callback_query(F.data.startswith("cl:"))
async def collection_cb(call: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    try:
        _, oid, tid, key, page = call.data.split(":")
        oid, tid, page = int(oid), int(tid), int(page)
    except Exception:
        await call.answer()
        return
    if call.from_user.id != oid:
        await call.answer("🔒 This isn't your menu. Use /collection to open yours!", show_alert=True)
        return
    if key == "n":
        await call.answer()
        return
    if key == "x":
        await call.answer()
        try:
            await call.message.delete()
        except Exception:
            pass
        return
    bid = bot_ctx.bot_id
    try:
        if key == "h" or key not in COLLECTIONS:
            target = call.from_user
            if tid != oid:
                from types import SimpleNamespace
                target = SimpleNamespace(id=tid, first_name=_col_name(call.message, tid))
            text, kb = await _col_home(pool, bid, oid, target)
        else:
            tname = call.from_user.first_name if tid == oid else _col_name(call.message, tid)
            text, kb = await _col_page(pool, bid, oid, tid, tname or "Player", key, page)
    except Exception:
        log.exception("collection page failed")
        await call.answer("⚠️ Try again.", show_alert=True)
        return
    await asyncio.gather(
        call.answer(),
        call.message.edit_text(text, parse_mode=H, reply_markup=kb),
        return_exceptions=True,
    )


def _col_name(msg, tid) -> str:
    # target name from the header of the current card ("NAME'S COLLECTION" or "NAME · ...")
    t = (msg.text or "") if msg else ""
    first = t.split("\n", 1)[0]
    for sep in ("'S COLLECTION", " · "):
        if sep in first:
            return first.split(sep, 1)[0].lstrip("🗂🎴 ").strip().title()
    return "Player"


@router.message(Command("harem", "chars"))
async def harem_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    rep = message.reply_to_message
    target = rep.from_user if rep and rep.from_user and not rep.from_user.is_bot else message.from_user
    if not target:
        return
    name = esc(target.first_name or "Player")
    try:
        await _ensure_harem(pool)
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT title, rarity, COUNT(*) OVER () AS n FROM ax_harem WHERE bot_id=$1 AND user_id=$2 "
                "ORDER BY id LIMIT 15", bot_ctx.bot_id, target.id)
    except Exception:
        log.exception("harem read failed")
        rows = []
    if not rows:
        await message.reply(f"🃏 <b>{name}</b> hasn't caught any characters yet! Use <code>/catch</code>.", parse_mode=H)
        return
    total = int(rows[0]["n"])
    lines = [f"{i}. {esc(r['title'])} ({esc(r['rarity'])})" for i, r in enumerate(rows, 1)]
    await message.reply(
        f"🃏 <b>{name.upper()}'S COLLECTION ({total})</b>\n\n<blockquote>" + "\n".join(lines) + "</blockquote>",
        parse_mode=H,
    )


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
        await _r(message, "No number guess is running right now.")
        return
    args = cmd_args(message)
    lo_all, hi_all = GUESS_RANGE
    if not args or not args[0].isdigit() or not (lo_all <= int(args[0]) <= hi_all):
        await _r(message, f"Use: <code>/g 250</code> (a number from {lo_all} to {hi_all})", parse_mode=H)
        return
    val = int(args[0])
    if val == ev["secret"]:
        if ACTIVE.pop(key, None) is None:
            return
        ok = await _pay(pool, bot_ctx.bot_id, message.from_user, ev["reward"], "guess", ev["tag"])
        who = esc(message.from_user.first_name or "Player")
        tail = f"won <code>+${ev['reward']:,}</code>!" if ok else "won, but the prize could not be paid. Please tell an admin."
        await _r(message, f"🎯 Correct! <b>{who}</b> guessed <code>{val}</code> and {tail}", parse_mode=H)
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
        await _r(message, f"{hint}\n💥 No tries left! The number was <b>{ev['secret']}</b>.", parse_mode=H)
        return
    await _r(message, f"{hint} It is between <code>{ev['lo']}</code> and <code>{ev['hi']}</code>. Tries left: <b>{left}</b>", parse_mode=H)


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


async def on_startup(bot: Bot, dispatcher) -> None:
    global _LOOP
    pool = dispatcher["pool"]
    _POOL[:] = [pool]
    _CTX[bot_context.resolve_bot_id(bot.id)] = {"bot": bot, "pool": pool}
    if _LOOP is None or _LOOP.done():
        _LOOP = asyncio.get_running_loop().create_task(_loop())


def register(dp) -> None:
    global _MW
    if not _MW:
        _MW = True
        dp.message.outer_middleware(ActivityMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

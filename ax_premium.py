"""Rumi Premium: 35 Stars, owner grant, wallet emoji (aiogram 3.x)."""
import logging
import time

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LabeledPrice, Message

from ax_adminpanel import OWNER_IDS
from ax_common import btn, cmd_args, esc

log = logging.getLogger(__name__)
router = Router(name="ax_premium")

H = ParseMode.HTML
PRICE = 35
_READY = [False]

_CACHE: dict[int, tuple[float, dict | None]] = {}
_CACHE_TTL = 120.0
_CACHE_MAX = 10_000


def _prune_cache(now: float) -> None:
    if len(_CACHE) <= _CACHE_MAX:
        return
    expired = [uid for uid, (ts, _) in _CACHE.items() if now - ts >= _CACHE_TTL]
    for uid in expired:
        _CACHE.pop(uid, None)
    if len(_CACHE) > _CACHE_MAX:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for uid, _ in oldest:
            _CACHE.pop(uid, None)


def invalidate(uid: int) -> None:
    _CACHE.pop(uid, None)


async def ensure(pool: asyncpg.Pool) -> None:
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_premium ("
            "user_id BIGINT PRIMARY KEY, emoji_id TEXT, emoji_char TEXT, "
            "since TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    _READY[0] = True


async def get_premium(pool: asyncpg.Pool, uid: int) -> dict | None:
    now = time.time()
    hit = _CACHE.get(uid)
    if hit and now - hit[0] < _CACHE_TTL:
        return dict(hit[1]) if hit[1] is not None else None
    await ensure(pool)
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT emoji_id, emoji_char FROM ax_premium WHERE user_id=$1",
            uid,
        )
    val = dict(row) if row is not None else None
    _prune_cache(now)
    _CACHE[uid] = (now, val)
    return dict(val) if val is not None else None


def emoji_html(row) -> str:
    if not row or not row["emoji_char"]:
        return ""
    ch = esc(row["emoji_char"])
    if row["emoji_id"]:
        return '<tg-emoji emoji-id="%s">%s</tg-emoji> ' % (row["emoji_id"], ch)
    return ch + " "


async def _grant(pool: asyncpg.Pool, uid: int) -> None:
    await ensure(pool)
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_premium (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            uid,
        )
    invalidate(uid)


def _target(message: Message) -> tuple[int | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, u.first_name or "Friend"
    args = cmd_args(message)
    if args and args[0].lstrip("-").isdigit():
        return int(args[0]), args[0]
    return None, None


@router.message(Command("premium"))
async def premium_cmd(message: Message, pool: asyncpg.Pool) -> None:
    if message.from_user is None:
        return
    row = await get_premium(pool, message.from_user.id)
    if row is not None:
        await message.reply(
            "💎 <b>You are Rumi Premium</b>\n<blockquote>Reply to an emoji "
            "with /setemoji to wear it before your name.</blockquote>",
            parse_mode=H,
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("⭐ Get Premium - %d Stars" % PRICE, "axp:buy", style="success")]]
    )
    await message.reply(
        "💎 <b>Rumi Premium</b>\n<blockquote>A bigger, cleaner wallet card "
        "and your own emoji before your name.</blockquote>",
        parse_mode=H,
        reply_markup=kb,
    )


@router.callback_query(F.data == "axp:buy")
async def buy(query: CallbackQuery, bot: Bot) -> None:
    await query.answer()
    if query.message is None:
        return
    await bot.send_invoice(
        chat_id=query.message.chat.id,
        title="Rumi Premium",
        description="Premium wallet and name emoji",
        payload="premium:%d" % query.from_user.id,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Rumi Premium", amount=PRICE)],
    )


@router.message(F.successful_payment.invoice_payload.startswith("premium:"))
async def paid(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        await _grant(pool, user.id)
    except Exception:
        log.exception("PREMIUM save failed")
    await message.reply(
        "💎 <b>Welcome to Premium, %s</b>\n<blockquote>Try /wallet, and reply "
        "to an emoji with /setemoji.</blockquote>" % esc(user.first_name or "Friend"),
        parse_mode=H,
    )


@router.message(Command("givepremium"))
async def givepremium(message: Message, pool: asyncpg.Pool) -> None:
    if message.from_user is None or message.from_user.id not in OWNER_IDS:
        return
    uid, name = _target(message)
    if uid is None:
        await message.reply("Use: /givepremium USER_ID (or reply)")
        return
    await _grant(pool, uid)
    await message.reply("💎 <b>%s</b> is now Premium" % esc(name), parse_mode=H)


@router.message(Command("removepremium"))
async def removepremium(message: Message, pool: asyncpg.Pool) -> None:
    if message.from_user is None or message.from_user.id not in OWNER_IDS:
        return
    uid, name = _target(message)
    if uid is None:
        await message.reply("Use: /removepremium USER_ID (or reply)")
        return
    await ensure(pool)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_premium WHERE user_id=$1", uid)
    invalidate(uid)
    await message.reply("Removed Premium from %s" % esc(name), parse_mode=H)


@router.message(Command("setemoji"))
async def setemoji(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    if await get_premium(pool, user.id) is None and user.id not in OWNER_IDS:
        await message.reply("💎 This is a Premium feature. Try /premium")
        return

    r = message.reply_to_message
    own = False
    if r is None or not (r.text or r.caption):
        r = message
        own = True

    text = r.text or r.caption or ""
    ents = r.entities or r.caption_entities or []
    eid, ch = None, None
    for e in ents:
        if e.type == "custom_emoji":
            raw = text.encode("utf-16-le")
            ch = raw[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le")
            eid = e.custom_emoji_id
            break

    t = text.strip()
    if own and t.startswith("/"):
        p = t.split(None, 1)
        t = p[1].strip() if len(p) > 1 else ""
    if eid is None and t and len(t) <= 8:
        ch = t

    if not ch:
        await message.reply("Reply to a message that has only an emoji, then send /setemoji")
        return

    if user.id in OWNER_IDS:
        await _grant(pool, user.id)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE ax_premium SET emoji_id=$2, emoji_char=$3 WHERE user_id=$1",
            user.id,
            eid,
            ch,
        )
    invalidate(user.id)
    note = "premium emoji ID found" if eid else "normal emoji only, no premium ID"
    await message.reply("✨ Emoji saved (%s). Check /wallet" % note)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

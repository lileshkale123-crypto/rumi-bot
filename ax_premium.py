"""Premium membership: Stars invoice, owner grant, wallet emoji (aiogram 3.x, bot_id & brand aware)."""
import logging
import time

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LabeledPrice, Message

import bot_context
from ax_adminpanel import OWNER_IDS
from ax_common import btn, cmd_args, esc
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="ax_premium")

H = ParseMode.HTML
PRICE = 35
_READY = [False]

_CACHE: dict[tuple[int, int], tuple[float, dict | None]] = {}
_CACHE_TTL = 120.0
_CACHE_MAX = 10_000


def _prune_cache(now: float) -> None:
    if len(_CACHE) <= _CACHE_MAX:
        return
    expired = [k for k, (ts, _) in _CACHE.items() if now - ts >= _CACHE_TTL]
    for k in expired:
        _CACHE.pop(k, None)
    if len(_CACHE) > _CACHE_MAX:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for k, _ in oldest:
            _CACHE.pop(k, None)


def invalidate(uid: int, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    _CACHE.pop((bid, uid), None)
    _CACHE.pop((0, uid), None)


async def ensure(pool: asyncpg.Pool) -> None:
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_premium ("
            "user_id BIGINT PRIMARY KEY, emoji_id TEXT, emoji_char TEXT, "
            "since TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "bot_id BIGINT NOT NULL DEFAULT 0)"
        )
    _READY[0] = True


async def get_premium(
    pool: asyncpg.Pool,
    uid: int,
    bot_id: int | None = None,
) -> dict | None:
    bid = bot_context.resolve_bot_id(bot_id)
    key = (bid, uid)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return dict(hit[1]) if hit[1] is not None else None
    await ensure(pool)
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT emoji_id, emoji_char FROM ax_premium WHERE bot_id=$1 AND user_id=$2",
            bid,
            uid,
        )
    val = dict(row) if row is not None else None
    _prune_cache(now)
    _CACHE[key] = (now, val)
    return dict(val) if val is not None else None


def emoji_html(row) -> str:
    if not row or not row["emoji_char"]:
        return ""
    ch = esc(row["emoji_char"])
    if row["emoji_id"]:
        return '<tg-emoji emoji-id="%s">%s</tg-emoji> ' % (row["emoji_id"], ch)
    return ch + " "


async def _grant(pool: asyncpg.Pool, uid: int, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    await ensure(pool)
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_premium (bot_id, user_id) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET bot_id = EXCLUDED.bot_id",
            bid,
            uid,
        )
    invalidate(uid, bot_id=bid)


def _target(message: Message) -> tuple[int | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, u.first_name or "Friend"
    args = cmd_args(message)
    if args and args[0].lstrip("-").isdigit():
        return int(args[0]), args[0]
    return None, None


@router.message(Command("premium"))
async def premium_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user is None:
        return
    bid = bot_ctx.bot_id
    bname = esc(get_brand(bid).name)
    row = await get_premium(pool, message.from_user.id, bot_id=bid)
    if row is not None:
        await message.reply(
            f"💎 <b>You are {bname} Premium</b>\n<blockquote>Reply to an emoji "
            "with /setemoji to wear it before your name.</blockquote>",
            parse_mode=H,
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("⭐ Get Premium - %d Stars" % PRICE, "axp:buy", style="success")]]
    )
    await message.reply(
        f"💎 <b>{bname} Premium</b>\n<blockquote>A bigger, cleaner wallet card "
        "and your own emoji before your name.</blockquote>",
        parse_mode=H,
        reply_markup=kb,
    )


@router.callback_query(F.data == "axp:buy")
async def buy(query: CallbackQuery, bot: Bot, bot_ctx: BotContext) -> None:
    await query.answer()
    if query.message is None:
        return
    bname = get_brand(bot_ctx.bot_id).name
    await bot.send_invoice(
        chat_id=query.message.chat.id,
        title=f"{bname} Premium",
        description="Premium wallet and name emoji",
        payload="premium:%d" % query.from_user.id,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{bname} Premium", amount=PRICE)],
    )


@router.message(F.successful_payment.invoice_payload.startswith("premium:"))
async def paid(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    try:
        await _grant(pool, user.id, bot_id=bid)
    except Exception:
        log.exception("PREMIUM save failed")
    await message.reply(
        "💎 <b>Welcome to Premium, %s</b>\n<blockquote>Try /wallet, and reply "
        "to an emoji with /setemoji.</blockquote>" % esc(user.first_name or "Friend"),
        parse_mode=H,
    )


@router.message(Command("givepremium"))
async def givepremium(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user is None or message.from_user.id not in OWNER_IDS:
        return
    uid, name = _target(message)
    if uid is None:
        await message.reply("Use: /givepremium USER_ID (or reply)")
        return
    await _grant(pool, uid, bot_id=bot_ctx.bot_id)
    await message.reply("💎 <b>%s</b> is now Premium" % esc(name), parse_mode=H)


@router.message(Command("removepremium"))
async def removepremium(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user is None or message.from_user.id not in OWNER_IDS:
        return
    uid, name = _target(message)
    if uid is None:
        await message.reply("Use: /removepremium USER_ID (or reply)")
        return
    bid = bot_ctx.bot_id
    await ensure(pool)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_premium WHERE bot_id=$1 AND user_id=$2", bid, uid)
    invalidate(uid, bot_id=bid)
    await message.reply("Removed Premium from %s" % esc(name), parse_mode=H)


@router.message(Command("setemoji"))
async def setemoji(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    if await get_premium(pool, user.id, bot_id=bid) is None and user.id not in OWNER_IDS:
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
        await _grant(pool, user.id, bot_id=bid)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE ax_premium SET emoji_id=$3, emoji_char=$4 WHERE bot_id=$1 AND user_id=$2",
            bid,
            user.id,
            eid,
            ch,
        )
    invalidate(user.id, bot_id=bid)
    note = "premium emoji ID found" if eid else "normal emoji only, no premium ID"
    await message.reply("✨ Emoji saved (%s). Check /wallet" % note)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

"""Premium membership: Stars invoice, owner grant, emoji & /check DM lookup (aiogram 3.x, bot_id & brand aware)."""
import logging
import time
from datetime import datetime, timezone

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
)

import bot_context
import config
import services
from ax_adminpanel import OWNER_IDS
from ax_common import btn, cmd_args, esc, get_bot_username
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


def emoji_html(row, fallback_emoji: str | None = None) -> str:
    """Render Premium emoji with safe fallback when custom/premium emoji is unavailable."""
    default_prem = fallback_emoji or getattr(config, "PREMIUM_DEFAULT_EMOJI", "💓")
    allow_custom = getattr(config, "PREMIUM_CUSTOM_EMOJI_ENABLED", True)
    if not row:
        return ""
    ch = (row.get("emoji_char") if isinstance(row, dict) else row["emoji_char"]) or default_prem
    eid = row.get("emoji_id") if isinstance(row, dict) else row["emoji_id"]
    if allow_custom and eid and ch:
        try:
            return '<tg-emoji emoji-id="%s">%s</tg-emoji> ' % (esc(str(eid)), esc(str(ch)))
        except Exception:
            return esc(str(ch)) + " "
    return esc(str(ch or default_prem)) + " "


def player_badge_html(prem_row: dict | None) -> str:
    """Returns Premium badge/emoji (💓 or custom) vs clean Normal emoji (👤)."""
    normal_emo = getattr(config, "NORMAL_DEFAULT_EMOJI", "👤")
    if prem_row is None:
        return esc(normal_emo)
    default_prem = getattr(config, "PREMIUM_DEFAULT_EMOJI", "💓")
    allow_custom = getattr(config, "PREMIUM_CUSTOM_EMOJI_ENABLED", True)
    ch = prem_row.get("emoji_char") or default_prem
    eid = prem_row.get("emoji_id")
    if allow_custom and eid and ch:
        return '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (esc(str(eid)), esc(str(ch)))
    return esc(str(ch or default_prem))


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


def _fmt_remaining(shield_until: datetime | None) -> str:
    if not shield_until:
        return "No active protection"
    dt = shield_until if shield_until.tzinfo else shield_until.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    secs = int((dt - now).total_seconds())
    if secs <= 0:
        return "Expired / No active protection"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


@router.message(Command("premium"))
async def premium_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user is None:
        return
    bid = bot_ctx.bot_id
    bname = esc(get_brand(bid).name)
    row = await get_premium(pool, message.from_user.id, bot_id=bid)
    if row is not None:
        emo = emoji_html(row)
        await message.reply(
            f"💓 <b>You are {bname} Premium</b>\n"
            f"<blockquote>"
            f"✨ <b>Badge:</b> {emo}<b>{esc(message.from_user.first_name or 'Member')}</b>\n"
            f"🛡 <b>Private Check:</b> Use <code>/check</code> (reply) for DM protection time\n"
            f"🎨 <b>Custom Emoji:</b> Reply to an emoji with <code>/setemoji</code>\n"
            f"💰 <b>Perks:</b> Higher limits, 30% wallet protection &amp; 5% tax"
            f"</blockquote>",
            parse_mode=H,
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("⭐ Get Premium - %d Stars" % PRICE, "axp:buy", style="success")]]
    )
    await message.reply(
        f"💎 <b>{bname} Premium</b>\n"
        f"<blockquote>"
        f"Normal users keep the clean standard UI (👤).\n"
        f"Premium unlocks 💓 / custom emoji, higher rob/kill limits, "
        f"30% wallet protection, 5% tax &amp; private <code>/check</code>."
        f"</blockquote>",
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
        description="Premium perks, badge and features",
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
        "💓 <b>Welcome to Premium, %s</b>\n<blockquote>Try /wallet, /check, and reply "
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
    await message.reply("💓 <b>%s</b> is now Premium" % esc(name), parse_mode=H)


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
        await message.reply(
            "💓 <b>Premium Feature</b>\n"
            "<blockquote>Only Premium users can use <code>/setemoji</code>. Use /premium to upgrade.</blockquote>",
            parse_mode=H,
        )
        return

    target_uid = user.id
    target_label = "your"
    if message.reply_to_message and message.reply_to_message.from_user:
        rp_user = message.reply_to_message.from_user
        if not rp_user.is_bot and rp_user.id != user.id:
            target_uid = rp_user.id
            target_label = f"{esc(rp_user.first_name or 'user')}'s"

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
        await message.reply(
            "✨ <b>How to use /setemoji</b>\n"
            "<blockquote>Send <code>/setemoji 💖</code> or reply to an emoji message with <code>/setemoji</code>. "
            "If custom emoji isn't active yet, normal emoji works automatically.</blockquote>",
            parse_mode=H,
        )
        return

    await _grant(pool, target_uid, bot_id=bid)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE ax_premium SET emoji_id=$3, emoji_char=$4 WHERE bot_id=$1 AND user_id=$2",
            bid,
            target_uid,
            eid,
            ch,
        )
    invalidate(target_uid, bot_id=bid)
    mode_txt = "custom emoji saved (with fallback)" if eid else "normal emoji saved"
    await message.reply(
        f"✨ <b>Emoji Updated</b>\n<blockquote>Set {target_label} badge emoji to <b>{esc(ch)}</b> ({mode_txt}).</blockquote>",
        parse_mode=H,
    )


@router.message(Command("check"))
async def check_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    bname = get_brand(bid).name
    uname = bot_ctx.username or await get_bot_username(bot)
    dm_url = f"https://t.me/{uname}" if uname else None

    if await get_premium(pool, user.id, bot_id=bid) is None and user.id not in OWNER_IDS:
        await message.reply(
            "💓 <b>Premium Only</b>\n"
            "<blockquote><code>/check</code> is a Premium command. Reply to a user with <code>/check</code> "
            "once you have Premium to receive their protection time privately in DM.</blockquote>",
            parse_mode=H,
        )
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "🛡 <b>How to use /check</b>\n"
            "<blockquote>Reply to a user's message in a group with <code>/check</code>. "
            "Their protection time will be sent privately to your DM.</blockquote>",
            parse_mode=H,
        )
        return

    target = message.reply_to_message.from_user
    w = await services.get_wallet(pool, target.id, bot_id=bid)
    shield_until = w["shield_until"] if w else None
    rem_str = _fmt_remaining(shield_until)
    active = rem_str not in ("No active protection", "Expired / No active protection")

    dm_text = (
        f"🛡 <b>PRIVATE PROTECTION CHECK</b>\n"
        f"<blockquote>"
        f"👤 <b>User:</b> {esc(target.first_name or 'Player')} (<code>{target.id}</code>)\n"
        f"🛡 <b>Status:</b> <b>{'Protected' if active else 'Unprotected'}</b>\n"
        f"⏳ <b>Time Left:</b> <code>{esc(rem_str)}</code>"
        f"</blockquote>"
    )

    try:
        await bot.send_message(user.id, dm_text, parse_mode=H)
        if message.chat.type != "private":
            kb = (
                InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=f"📩 Open {bname} DM", url=dm_url)]]
                )
                if dm_url
                else None
            )
            await message.reply(
                "🔒 <b>Protection info sent to your DM</b>\n"
                "<blockquote>Kept private from the group chat. Tap below to view in DM.</blockquote>",
                parse_mode=H,
                reply_markup=kb,
            )
    except Exception:
        kb = (
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=f"📩 Start {bname} in DM", url=dm_url)]]
            )
            if dm_url
            else None
        )
        await message.reply(
            "⚠️ <b>Could not message your DM yet</b>\n"
            "<blockquote>Tap the button below, press <b>Start</b> in my DM once, "
            "and then use <code>/check</code> again.</blockquote>",
            parse_mode=H,
            reply_markup=kb,
        )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

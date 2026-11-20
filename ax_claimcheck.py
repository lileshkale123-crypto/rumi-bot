"""Emoji check before the daily /claim pays (aiogram 3.x, bot_id aware)."""
import logging
import random
import secrets
import time
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import bot_context
import services
import ui
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_claimcheck")

H = ParseMode.HTML
TTL = 90              # seconds a check stays valid
MIN_THINK = 1.0       # answers faster than this are treated as a script
PASS_TTL = 30         # seconds the "verified" pass lasts
MAX_FAILS = 3         # wrong answers allowed ...
FAIL_WINDOW = 600     # ... inside this many seconds

EMOJI = {
    "\U0001f319": "moon", "\u2b50": "star", "\U0001f338": "flower",
    "\U0001f525": "fire", "\U0001f340": "clover", "\U0001f48e": "gem",
    "\U0001f388": "balloon", "\U0001f98b": "butterfly", "\U0001f353": "strawberry",
    "\U0001f431": "cat", "\U0001f3b5": "music note", "\U0001f308": "rainbow",
}

_pending: dict[str, tuple[int, str, float, list[str]]] = {}
_passed: dict[tuple[int, int], float] = {}
_fails: dict[tuple[int, int], list] = {}


def _purge(now: float) -> None:
    if len(_pending) > 2000:
        for k in [k for k, v in _pending.items() if now - v[2] > TTL]:
            _pending.pop(k, None)
    if len(_passed) > 5000:
        for k in [k for k, v in _passed.items() if now - v > PASS_TTL]:
            _passed.pop(k, None)
    if len(_fails) > 5000:
        for k in [k for k, v in _fails.items() if now - v[1] > FAIL_WINDOW]:
            _fails.pop(k, None)


def _locked_for(key: tuple[int, int], now: float) -> int:
    f = _fails.get(key)
    if not f:
        return 0
    if now - f[1] > FAIL_WINDOW:
        _fails.pop(key, None)
        return 0
    if f[0] >= MAX_FAILS:
        return int(FAIL_WINDOW - (now - f[1]))
    return 0


def _build(uid: int, now: float) -> tuple[str, InlineKeyboardMarkup]:
    shown = random.sample(list(EMOJI), 6)
    correct = random.choice(shown)
    token = secrets.token_hex(4)
    _pending[token] = (uid, correct, now, shown)
    rows = [
        [
            InlineKeyboardButton(text=e, callback_data=f"ccv:{token}:{i}")
            for i, e in enumerate(shown[r * 3 : (r + 1) * 3], start=r * 3)
        ]
        for r in range(2)
    ]
    text = (
        "\U0001f510 <b>Quick check</b>\n"
        f"<blockquote>Tap the <b>{EMOJI[correct]}</b> to confirm you're a real person.</blockquote>"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def gate(
    user,
    msg: Message | None,
    pool: asyncpg.Pool,
    query: CallbackQuery | None = None,
    bot_id: int | None = None,
) -> bool:
    """True if we showed a check (caller stops). False = go ahead and pay."""
    try:
        if user is None or msg is None:
            return False
        bid = bot_context.resolve_bot_id(bot_id)
        key = (bid, user.id)
        now = time.monotonic()
        _purge(now)

        ts = _passed.pop(key, None)
        if ts is not None and now - ts < PASS_TTL:
            return False

        import ax_premium

        if await ax_premium.get_premium(pool, user.id):
            return False

        if query is not None:
            try:
                await query.answer()
            except Exception:
                pass

        wait = _locked_for(key, now)
        if wait:
            await msg.reply(
                f"\u23f3 <b>Too many wrong taps</b>\nTry /claim again in {wait // 60 + 1} min.",
                parse_mode=H,
            )
            return True

        today = datetime.now(services.IST).date()
        last = await pool.fetchval(
            "SELECT last_claim_date FROM streaks WHERE bot_id=$1 AND user_id=$2",
            bid,
            user.id,
        )
        if last == today:
            nxt = datetime.combine(
                today + timedelta(days=1),
                datetime.min.time(),
                tzinfo=services.IST,
            )
            await msg.reply(ui.reward_cooldown_card(nxt), parse_mode=H)
            return True

        text, kb = _build(user.id, now)
        await msg.reply(text, parse_mode=H, reply_markup=kb)
        return True
    except Exception:
        log.exception("claim check gate failed")
        return False


@router.callback_query(F.data.startswith("ccv:"))
async def answer_cb(
    query: CallbackQuery,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await query.answer()
        return
    _, token, idx_str = parts
    idx = int(idx_str)
    now = time.monotonic()
    entry = _pending.get(token)

    if entry is None or now - entry[2] > TTL:
        _pending.pop(token, None)
        await query.answer("This check expired. Send /claim again.", show_alert=True)
        return
    uid, correct, created, shown = entry
    if query.from_user.id != uid:
        await query.answer("This check is for someone else.", show_alert=True)
        return
    _pending.pop(token, None)

    if now - created < MIN_THINK:
        await query.answer("Too fast. Send /claim and try again.", show_alert=True)
        return

    bid = bot_ctx.bot_id
    key = (bid, uid)
    if idx >= len(shown) or shown[idx] != correct:
        f = _fails.setdefault(key, [0, now])
        if now - f[1] > FAIL_WINDOW:
            f[0], f[1] = 0, now
        f[0] += 1
        left = MAX_FAILS - f[0]
        hint = f" {left} tries left." if left > 0 else " Locked for 10 minutes."
        await query.answer("Not that one." + hint, show_alert=True)
        if query.message:
            try:
                await query.message.edit_text("\u274c Check failed. Send /claim to try again.")
            except Exception:
                pass
        return

    _fails.pop(key, None)
    _passed[key] = now
    await query.answer()
    if query.message:
        try:
            await query.message.edit_text("\u2705 Verified")
        except Exception:
            pass
    import handlers

    await handlers._execute_claim(query.from_user, query.message, bot, pool, bot_id=bid)


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

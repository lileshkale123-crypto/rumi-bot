"""Emoji check before the daily /claim pays. No web page, no domain, no setup.

Non-premium users get a small "tap the <emoji name>" check. Only the person who
asked can answer, each check works once, and answering instantly (under a
second) or wrongly too many times is refused. Premium users skip the check.
"""
import logging
import random
import secrets
import time
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler

import services
import ui

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

_pending = {}   # token -> (user_id, correct_emoji, created_monotonic, shown_order)
_passed = {}    # user_id -> monotonic time of a successful check
_fails = {}     # user_id -> [count, first_monotonic]


def _purge(now):
    if len(_pending) > 2000:
        for k in [k for k, v in _pending.items() if now - v[2] > TTL]:
            _pending.pop(k, None)
    if len(_passed) > 5000:
        for k in [k for k, v in _passed.items() if now - v > PASS_TTL]:
            _passed.pop(k, None)
    if len(_fails) > 5000:
        for k in [k for k, v in _fails.items() if now - v[1] > FAIL_WINDOW]:
            _fails.pop(k, None)


def _locked_for(uid, now):
    f = _fails.get(uid)
    if not f:
        return 0
    if now - f[1] > FAIL_WINDOW:
        _fails.pop(uid, None)
        return 0
    if f[0] >= MAX_FAILS:
        return int(FAIL_WINDOW - (now - f[1]))
    return 0


def _build(uid, now):
    shown = random.sample(list(EMOJI), 6)
    correct = random.choice(shown)
    token = secrets.token_hex(4)
    _pending[token] = (uid, correct, now, shown)
    rows = [[InlineKeyboardButton(e, callback_data=f"ccv:{token}:{i}")
             for i, e in enumerate(shown[r * 3:(r + 1) * 3], start=r * 3)]
            for r in range(2)]
    text = ("\U0001f510 <b>Quick check</b>\n"
            f"<blockquote>Tap the <b>{EMOJI[correct]}</b> to confirm you're a real person.</blockquote>")
    return text, InlineKeyboardMarkup(rows)


async def gate(update, context):
    """True if we showed a check (caller stops). False = go ahead and pay."""
    try:
        user = update.effective_user
        msg = update.effective_message
        if user is None or msg is None:
            return False
        now = time.monotonic()
        _purge(now)

        # a fresh successful check lets exactly one claim through
        ts = _passed.pop(user.id, None)
        if ts is not None and now - ts < PASS_TTL:
            return False

        pool = context.bot_data["pool"]
        import ax_premium
        if await ax_premium.get_premium(pool, user.id):
            return False

        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

        wait = _locked_for(user.id, now)
        if wait:
            await msg.reply_html(
                f"\u23f3 <b>Too many wrong taps</b>\nTry /claim again in {wait // 60 + 1} min.")
            return True

        today = datetime.now(services.IST).date()
        last = await pool.fetchval("SELECT last_claim_date FROM streaks WHERE user_id=$1", user.id)
        if last == today:
            nxt = datetime.combine(today + timedelta(days=1), datetime.min.time(),
                                   tzinfo=services.IST)
            await msg.reply_html(ui.reward_cooldown_card(nxt))
            return True

        text, kb = _build(user.id, now)
        await msg.reply_html(text, reply_markup=kb)
        return True
    except Exception:
        logging.exception("claim check gate failed")
        return False


async def answer_cb(update, context):
    q = update.callback_query
    if q is None or not q.data:
        return
    parts = q.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await q.answer()
        return
    _, token, idx = parts
    idx = int(idx)
    now = time.monotonic()
    entry = _pending.get(token)

    if entry is None or now - entry[2] > TTL:
        _pending.pop(token, None)
        await q.answer("This check expired. Send /claim again.", show_alert=True)
        return
    uid, correct, created, shown = entry
    if q.from_user.id != uid:
        await q.answer("This check is for someone else.", show_alert=True)
        return
    _pending.pop(token, None)          # single use

    if now - created < MIN_THINK:
        await q.answer("Too fast. Send /claim and try again.", show_alert=True)
        return
    if idx >= len(shown) or shown[idx] != correct:
        f = _fails.setdefault(uid, [0, now])
        if now - f[1] > FAIL_WINDOW:
            f[0], f[1] = 0, now
        f[0] += 1
        left = MAX_FAILS - f[0]
        hint = f" {left} tries left." if left > 0 else " Locked for 10 minutes."
        await q.answer("Not that one." + hint, show_alert=True)
        try:
            await q.message.edit_text("\u274c Check failed. Send /claim to try again.")
        except Exception:
            pass
        return

    _fails.pop(uid, None)
    _passed[uid] = now
    try:
        await q.message.edit_text("\u2705 Verified")
    except Exception:
        pass
    import handlers
    await handlers.claim_command(update, context)


def register(app):
    app.add_handler(CallbackQueryHandler(answer_cb, pattern=r"^ccv:"), group=-7)

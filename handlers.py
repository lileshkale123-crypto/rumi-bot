"""
Telegram command and callback handlers for Rumi.

Every handler follows the same shape: ensure the caller has a wallet,
call the relevant service function, render the result with ui.py.
"""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
import services
import ui

PLACEHOLDER_PHOTO_URL = "https://raw.githubusercontent.com/lileshkale123-crypto/rumi-bot/main/assets/rumi-start-v2.png"

COMMAND_LIST = [
    ("/wallet", "Check your Wallet"),
    ("/claim", "Claim your daily Reward"),
    ("/pay", "Send coins to a friend"),
    ("/guard", "Raise a protective Guard"),
    ("/rob", "Attempt a heist on someone"),
    ("/rankings", "View the Rankings"),
    ("/bluff", "Play Shadow Bluff with friends"),
]

CATEGORY_META = {
    "cash": {"title": "Richest", "emoji": "💵", "is_currency": True},
    "xp": {"title": "Top XP", "emoji": "⭐", "is_currency": False},
    "gems": {"title": "Most Gems", "emoji": "💎", "is_currency": False},
}


def _get_reply_target(update: Update):
    msg = update.effective_message
    if msg and msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        return msg.reply_to_message.from_user
    return None


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = context.bot_data["pool"]
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)
    wallet = await services.get_wallet(pool, user.id)
    name = user.first_name or user.username or "Traveler"

    text = ui.start_card(name, wallet, COMMAND_LIST)
    keyboard = ui.start_keyboard(context.bot.username)

    await update.message.reply_photo(
        photo=PLACEHOLDER_PHOTO_URL, caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def features_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await update.effective_message.reply_text("🌟 More features are coming soon — stay tuned!")


async def games_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await update.effective_message.reply_html(ui.games_card())


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


# ---------------------------------------------------------------------------
# /wallet (balance)
# ---------------------------------------------------------------------------

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = context.bot_data["pool"]
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)
    wallet = await services.get_wallet(pool, user.id)
    name = user.first_name or user.username or "Traveler"

    text = ui.wallet_card(name, wallet)
    keyboard = ui.wallet_keyboard()

    if update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception:
            pass  # identical content or message too old to edit — ignore
        await query.answer()
        return

    await update.message.reply_html(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# /claim (daily reward)
# ---------------------------------------------------------------------------

async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = context.bot_data["pool"]
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)

    result = await services.claim_daily(pool, user.id)

    if update.callback_query:
        await update.callback_query.answer()

    if result["status"] == "cooldown":
        await update.effective_message.reply_html(ui.reward_cooldown_card(result["next_claim_at"]))
        return
    if result["status"] == "already_claimed_race":
        await update.effective_message.reply_html(ui.error_card("You already claimed your reward just now."))
        return

    await update.effective_message.reply_html(
        ui.reward_claimed_card(
            result["cash_reward"],
            result["gem_reward"],
            result["lucky_bonus"],
            result["new_streak"],
            result["milestone_hit"],
            result["is_weekly_bonus"],
            result.get("xp"),
        )
    )


# ---------------------------------------------------------------------------
# /pay
# ---------------------------------------------------------------------------

async def _respond_to_pay_result(update: Update, result: dict, amount: int, target_label: str) -> None:
    status = result["status"]
    if status == "ok":
        await update.message.reply_html(ui.payment_success_card(amount, target_label, result["new_sender_balance"], result["tax"], result["received"]))
    elif status == "self_transfer":
        await update.message.reply_html(ui.error_card("You can't pay yourself."))
    elif status == "invalid_amount":
        await update.message.reply_html(ui.error_card("Enter a valid positive whole amount."))
    elif status == "below_minimum":
        await update.message.reply_html(ui.error_card("That's below the minimum transfer amount."))
    elif status == "above_maximum":
        await update.message.reply_html(ui.error_card("That exceeds the maximum transfer amount."))
    elif status == "insufficient_funds":
        await update.message.reply_html(ui.error_card("You don't have enough balance for this transfer."))
    elif status == "daily_limit_exceeded":
        await update.message.reply_html(ui.error_card("You've hit your daily transfer limit."))
    elif status == "receiver_not_found":
        await update.message.reply_html(ui.error_card("That user hasn't used Rumi yet."))
    else:
        await update.message.reply_html(ui.error_card("Something went wrong. Please try again."))

async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = context.bot_data["pool"]
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)

    args = context.args
    reply_user = _get_reply_target(update)

    # Reply mode: user replies to someone's message with "/pay 100"
    if reply_user:
        if len(args) < 1:
            await update.message.reply_html(ui.error_card("Reply to their message with: /pay amount"))
            return
        try:
            amount = int(args[0])
        except ValueError:
            await update.message.reply_html(ui.error_card("Enter a valid amount."))
            return
        await services.ensure_user_and_wallet(pool, reply_user.id, reply_user.username, reply_user.first_name)
        target_label = reply_user.first_name or reply_user.username or "them"
        result = await services.pay_to_id(pool, user.id, reply_user.id, amount)
        await _respond_to_pay_result(update, result, amount, target_label)
        return

    # Argument mode: "/pay @username 100" or "/pay 123456789 100"
    if len(args) < 2:
        await update.message.reply_html(
            ui.error_card("Usage: /pay @username amount, /pay user_id amount, or reply to a message with /pay amount")
        )
        return

    raw_target = args[0].lstrip("@")
    try:
        amount = int(args[1])
    except ValueError:
        await update.message.reply_html(ui.error_card("Usage: /pay @username amount"))
        return

    if raw_target.isdigit():
        result = await services.pay_to_id(pool, user.id, int(raw_target), amount)
    else:
        result = await services.pay(pool, user.id, raw_target, amount)

    await _respond_to_pay_result(update, result, amount, raw_target)


# ---------------------------------------------------------------------------
# /guard (shield)
# ---------------------------------------------------------------------------

async def guard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = context.bot_data["pool"]
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)

    result = await services.apply_ward(pool, user.id)
    if result["status"] == "ok":
        await update.message.reply_html(ui.guard_success_card(result["shield_until"]))
    elif result["status"] == "insufficient_funds":
        await update.message.reply_html(ui.guard_fail_card(result["required"], result["available"]))
    elif result["status"] == "already_shielded":
        await update.message.reply_html(ui.guard_already_active_card(result["shield_until"]))
    else:
        await update.message.reply_html(ui.error_card("Something went wrong. Please try again."))


# ---------------------------------------------------------------------------
# /rob
# ---------------------------------------------------------------------------

async def rob_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = context.bot_data["pool"]
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)

    reply_user = _get_reply_target(update)
    args = context.args

    amount_arg = args[0] if reply_user and args else (args[1] if len(args) > 1 else None)
    try:
        amount = int(amount_arg)
    except (TypeError, ValueError):
        await update.message.reply_html(ui.error_card("Usage: reply with /rob amount, or /rob @username amount, or /rob user_id amount"))
        return

    if reply_user:
        victim_id = reply_user.id
        victim_name = reply_user.first_name or reply_user.username or "them"
        await services.ensure_user_and_wallet(pool, victim_id, reply_user.username, reply_user.first_name)
    elif args:
        raw_target = args[0].lstrip("@")
        if raw_target.isdigit():
            victim_id = int(raw_target)
            wallet = await services.get_wallet(pool, victim_id)
            if wallet is None:
                await update.message.reply_html(ui.error_card("That user hasn't used Rumi yet."))
                return
        else:
            wallet = await services.get_wallet_by_username(pool, raw_target)
            if wallet is None:
                await update.message.reply_html(ui.error_card("That user hasn't used Rumi yet."))
                return
            victim_id = wallet["id"]
        victim_name = await services.get_display_name(pool, victim_id)
    else:
        await update.message.reply_html(ui.error_card("Usage: /rob @username, /rob user_id, or reply to a message with /rob"))
        return

    result = await services.rob(pool, user.id, victim_id, amount)
    status = result["status"]

    if status == "success":
        await update.message.reply_html(ui.rob_success_card(result["amount"], victim_name, result["tax"], result["net"]) + ui.xp_line(result.get("xp")))
    elif status == "failed":
        await update.message.reply_html(ui.rob_fail_card(result["penalty"], victim_name) + ui.xp_line(result.get("xp")))
    elif status == "self_rob":
        await update.message.reply_html(ui.error_card("You can't rob yourself."))
    elif status == "cooldown":
        await update.message.reply_html(ui.rob_cooldown_card(result["retry_at"]))
    elif status == "victim_not_found":
        await update.message.reply_html(ui.error_card("That user hasn't used Rumi yet."))
    elif status == "victim_shielded":
        await update.message.reply_html(ui.rob_victim_shielded_card())
    elif status == "victim_too_poor":
        await update.message.reply_html(ui.rob_victim_too_poor_card())
    elif status == "amount_too_high":
        await update.message.reply_html(ui.error_card(f"You can rob at most {config.CURRENCY_SYMBOL}{result['max_allowed']} from them right now."))
    elif status == "amount_too_low":
        await update.message.reply_html(ui.error_card(f"The minimum rob amount is {config.CURRENCY_SYMBOL}{config.ROB_MIN_AMOUNT}."))
    elif status == "invalid_amount":
        await update.message.reply_html(ui.error_card("Enter a valid positive whole amount."))
    else:
        await update.message.reply_html(ui.error_card("Something went wrong. Please try again."))


# ---------------------------------------------------------------------------
# /rankings (leaderboard)
# ---------------------------------------------------------------------------

async def _render_leaderboard(pool, category: str, page: int):
    rows, total = await services.get_leaderboard(pool, category, page, config.LEADERBOARD_PAGE_SIZE)
    total_pages = max(1, -(-total // config.LEADERBOARD_PAGE_SIZE))  # ceiling division
    meta = CATEGORY_META[category]
    entries = [{"name": r["name"], "value": r["value"]} for r in rows]
    text = ui.leaderboard_card(meta["title"], meta["emoji"], entries, page, total_pages, meta["is_currency"])
    keyboard = ui.leaderboard_keyboard(category, page, total_pages)
    return text, keyboard


async def rankings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool = context.bot_data["pool"]
    text, keyboard = await _render_leaderboard(pool, "cash", 1)
    await update.message.reply_html(text, reply_markup=keyboard)


async def rankings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pool = context.bot_data["pool"]
    _, category, page_str = query.data.split(":")

    if category not in ("cash", "xp", "gems"):
        await query.answer()
        return

    page = max(1, int(page_str))
    text, keyboard = await _render_leaderboard(pool, category, page)
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        pass  # identical content or message too old to edit — ignore
    await query.answer()

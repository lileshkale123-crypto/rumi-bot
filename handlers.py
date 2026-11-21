"""
Core bot command handlers: /start, /wallet, /claim, /pay, /guard, /rob, /rankings (aiogram 3.x, bot_id & brand aware).
"""

import html
import logging
import random
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import ax_common
import bot_context
import config
import services
import ui
from bot_context import BotContext, get_brand

logger = logging.getLogger(__name__)
router = Router(name="handlers")

PLACEHOLDER_PHOTO_URL = "https://files.catbox.moe/9y1x7c.jpg"

COMMAND_LIST = [
    ("/start", "Start the bot & view snapshot"),
    ("/wallet", "Check cash, gems & XP"),
    ("/claim", "Claim daily reward"),
    ("/pay", "Send cash to another user"),
    ("/guard", "Buy 24h robbery shield"),
    ("/rob", "Attempt a heist on someone"),
    ("/rankings", "Top cash, XP & gems"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_reply_target(message: Message | None):
    if message and message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if not target.is_bot:
            return target
    return None


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def start_command(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    user = message.from_user
    if user is None:
        return

    bid = bot_ctx.bot_id
    brand = get_brand(bid)
    bot_username = bot_ctx.username or await ax_common.get_bot_username(bot)

    if message.chat.type in ("group", "supergroup"):
        fn = html.escape(user.first_name or "friend")
        bname = html.escape(brand.name)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"{brand.emoji} Start {brand.name} in DM", url=f"https://t.me/{bot_username}")]
            ]
        )
        await message.reply(
            f"<b>Hey {fn}</b> {brand.emoji}\n"
            f"<blockquote>I'm {bname}. Tap below to start me in DM for your wallet, games and the full guide ✨</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    wallet = await services.get_wallet(pool, user.id, bot_id=bid)
    name = user.first_name or user.username or "Traveler"

    text = ui.start_card(name, wallet, COMMAND_LIST, bot_id=bid)
    keyboard = ui.start_keyboard(bot_username, bot_id=bid)

    try:
        await message.reply_photo(
            photo=PLACEHOLDER_PHOTO_URL,
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data == "games")
async def games_callback(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.answer(ui.games_menu_text(), parse_mode=ParseMode.HTML, reply_markup=ui.games_menu_keyboard())


@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(F.data == "startmenu")
async def features_callback(query: CallbackQuery, bot: Bot, bot_ctx: BotContext) -> None:
    await query.answer()
    if query.message:
        uname = bot_ctx.username or await ax_common.get_bot_username(bot)
        await query.message.answer(
            ui.features_menu_text(bot_id=bot_ctx.bot_id),
            parse_mode=ParseMode.HTML,
            reply_markup=ui.features_menu_keyboard(uname, bot_id=bot_ctx.bot_id),
        )


# ---------------------------------------------------------------------------
# /wallet (balance)
# ---------------------------------------------------------------------------

async def _build_wallet_view(pool: asyncpg.Pool, user, reply_target=None, bot_id: int | None = None):
    bid = bot_context.resolve_bot_id(bot_id)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)

    other = False
    target_uid = user.id
    if reply_target and reply_target.id != user.id:
        await services.ensure_user_and_wallet(
            pool, reply_target.id, reply_target.username, reply_target.first_name, bot_id=bid
        )
        target_uid = reply_target.id
        name = reply_target.first_name or reply_target.username or "Traveler"
        other = True
    else:
        name = user.first_name or user.username or "Traveler"

    wallet = await services.get_wallet(pool, target_uid, bot_id=bid)
    text = ui.wallet_card(name, wallet, full=other, bot_id=bid)
    keyboard = None if other else ui.wallet_keyboard()

    import ax_premium

    prem = await ax_premium.get_premium(pool, target_uid, bot_id=bid)
    if prem is not None:
        text = ui.wallet_card_premium(name, wallet, ax_premium.emoji_html(prem), full=other, bot_id=bid)
        keyboard = None

    return text, keyboard


@router.message(Command("wallet"))
async def wallet_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    text, keyboard = await _build_wallet_view(
        pool, user, _get_reply_target(message), bot_id=bot_ctx.bot_id
    )
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data == "wallet:refresh")
async def wallet_refresh_callback(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    text, keyboard = await _build_wallet_view(pool, query.from_user, None, bot_id=bot_ctx.bot_id)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer()


# ---------------------------------------------------------------------------
# /claim (daily reward)
# ---------------------------------------------------------------------------

async def _execute_claim(
    user,
    target_message: Message | None,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_id: int | None = None,
) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    result = await services.claim_daily(pool, user.id, bot_id=bid)
    status = result["status"]

    if status == "already_claimed":
        text = ui.already_claimed_card(result["next_claim_at"], bot_id=bid)
    elif status == "claimed":
        wallet = await services.get_wallet(pool, user.id, bot_id=bid)
        name = user.first_name or user.username or "Traveler"
        text = ui.reward_claimed_card(
            name=name,
            cash_reward=result["cash_reward"],
            gem_reward=result["gem_reward"],
            streak=result["streak"],
            milestone_bonus=result["milestone_bonus"],
            new_cash=wallet["cash"],
            xp_info=result.get("xp"),
            bot_id=bid,
        )
        try:
            import ax_referral

            await ax_referral._pay_on_first_claim(pool, bot, user.id, bot_id=bid)
        except Exception:
            pass
    else:
        text = ui.error_card("Could not process claim right now. Please try again.", bot_id=bid)

    if target_message:
        await target_message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("claim"))
async def claim_command(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    user = message.from_user
    if user is None:
        return
    await _execute_claim(user, message, bot, pool, bot_id=bot_ctx.bot_id)


@router.callback_query(F.data == "claim")
async def claim_callback(
    query: CallbackQuery,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    await query.answer()
    await _execute_claim(query.from_user, query.message, bot, pool, bot_id=bot_ctx.bot_id)


# ---------------------------------------------------------------------------
# /pay (transfer)
# ---------------------------------------------------------------------------

async def _respond_to_pay_result(message: Message, result: dict, bot_id: int | None = None) -> None:
    status = result["status"]
    if status == "ok":
        text = ui.transfer_success_card(
            recipient_name=result["recipient_name"],
            amount_sent=result["amount_sent"],
            tax_deducted=result["tax_deducted"],
            amount_received=result["amount_received"],
            new_sender_balance=result["new_sender_balance"],
            bot_id=bot_id,
        )
    elif status == "self_transfer":
        text = ui.error_card("You cannot pay yourself.", bot_id=bot_id)
    elif status == "below_min":
        text = ui.error_card(
            f"Minimum transfer is {config.CURRENCY_SYMBOL}{config.MIN_TRANSFER}.", bot_id=bot_id
        )
    elif status == "above_max":
        text = ui.error_card(
            f"Maximum single transfer is {config.CURRENCY_SYMBOL}{config.MAX_TRANSFER:,}.",
            bot_id=bot_id,
        )
    elif status == "insufficient_funds":
        text = ui.error_card(
            f"Insufficient funds. You have {config.CURRENCY_SYMBOL}{result['available']:,}.",
            bot_id=bot_id,
        )
    elif status == "daily_limit":
        text = ui.error_card(
            f"Daily transfer limit reached ({config.CURRENCY_SYMBOL}{config.DAILY_TRANSFER_LIMIT:,}/day). "
            f"Remaining today: {config.CURRENCY_SYMBOL}{result['remaining']:,}.",
            bot_id=bot_id,
        )
    else:
        text = ui.error_card("Could not complete transfer.", bot_id=bot_id)

    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("pay"))
async def pay_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    sender = message.from_user
    if sender is None:
        return
    bid = bot_ctx.bot_id
    target = _get_reply_target(message)
    args = ax_common.cmd_args(message)

    if target is None or not args:
        await message.reply(
            ui.error_card("Reply to a user's message with /pay &lt;amount&gt;.", bot_id=bid),
            parse_mode=ParseMode.HTML,
        )
        return

    raw_amount = args[0].replace(",", "").lstrip(config.CURRENCY_SYMBOL)
    if not raw_amount.isdigit():
        await message.reply(
            ui.error_card("Enter a valid positive number. Example: /pay 250", bot_id=bid),
            parse_mode=ParseMode.HTML,
        )
        return

    amount = int(raw_amount)
    await services.ensure_user_and_wallet(pool, sender.id, sender.username, sender.first_name, bot_id=bid)
    await services.ensure_user_and_wallet(pool, target.id, target.username, target.first_name, bot_id=bid)

    result = await services.transfer_cash(
        pool=pool,
        sender_id=sender.id,
        recipient_id=target.id,
        recipient_name=target.first_name or target.username or "Traveler",
        amount=amount,
        bot_id=bid,
    )
    await _respond_to_pay_result(message, result, bot_id=bid)


# ---------------------------------------------------------------------------
# /guard (24h shield)
# ---------------------------------------------------------------------------

@router.message(Command("guard"))
async def guard_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    result = await services.buy_shield(pool, user.id, bot_id=bid)

    if result["status"] == "ok":
        text = ui.shield_activated_card(result["shield_until"], result["new_balance"], bot_id=bid)
    elif result["status"] == "insufficient_funds":
        text = ui.error_card(
            f"You need {config.CURRENCY_SYMBOL}{result['required']:,} for a 24h guard "
            f"(you have {config.CURRENCY_SYMBOL}{result['available']:,}).",
            bot_id=bid,
        )
    else:
        text = ui.error_card("Could not activate guard right now.", bot_id=bid)

    await message.reply(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /rob (heist)
# ---------------------------------------------------------------------------

@router.message(Command("rob"))
async def rob_command(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_ctx: BotContext,
) -> None:
    robber = message.from_user
    if robber is None:
        return
    bid = bot_ctx.bot_id
    victim = _get_reply_target(message)
    args = ax_common.cmd_args(message)

    if victim is None:
        await message.reply(
            ui.error_card("Reply to a user's message with /rob &lt;amount&gt;.", bot_id=bid),
            parse_mode=ParseMode.HTML,
        )
        return

    amount = None
    if args:
        raw = args[0].replace(",", "").lstrip(config.CURRENCY_SYMBOL)
        if not raw.isdigit() or int(raw) <= 0:
            await message.reply(
                ui.error_card("Enter a valid positive amount. Example: /rob 500", bot_id=bid),
                parse_mode=ParseMode.HTML,
            )
            return
        amount = int(raw)
    else:
        amount = random.randint(config.ROB_MIN_AMOUNT, min(500, config.ROB_MAX_AMOUNT))

    await services.ensure_user_and_wallet(pool, robber.id, robber.username, robber.first_name, bot_id=bid)
    await services.ensure_user_and_wallet(pool, victim.id, victim.username, victim.first_name, bot_id=bid)

    result = await services.rob_user(
        pool=pool,
        robber_id=robber.id,
        victim_id=victim.id,
        victim_name=victim.first_name or victim.username or "Traveler",
        amount=amount,
        message_id=message.message_id,
        bot_id=bid,
    )
    status = result["status"]
    victim_name = victim.first_name or victim.username or "Traveler"

    if status == "self_rob":
        text = ui.error_card("You cannot rob yourself.", bot_id=bid)
    elif status == "robber_shielded":
        text = ui.error_card(
            "You cannot rob while your own /guard shield is active. Drop it or wait for it to expire.",
            bot_id=bid,
        )
    elif status == "victim_shielded":
        text = ui.rob_shielded_card(victim_name, bot_id=bid)
    elif status == "victim_broke":
        text = ui.error_card(f"{html.escape(victim_name)} doesn't have enough cash to rob.", bot_id=bid)
    elif status == "already_robbed_msg":
        text = ui.error_card("Someone already robbed this message while the victim was low on cash.", bot_id=bid)
    elif status == "cooldown":
        rem = result["retry_at"] - datetime.now(timezone.utc)
        mins = max(1, int(rem.total_seconds() // 60) + 1)
        text = ui.error_card(f"Rob cooldown active. Try again in ~{mins}m.", bot_id=bid)
    elif status == "success":
        text = ui.rob_success_card(
            victim_name=victim_name,
            stolen=result["stolen"],
            tax=result["tax"],
            net=result["net"],
            new_balance=result["new_balance"],
            xp_info=result.get("xp"),
            bot_id=bid,
        )
    elif status == "caught":
        text = ui.rob_caught_card(
            victim_name=victim_name,
            penalty=result["penalty"],
            new_balance=result["new_balance"],
            xp_info=result.get("xp"),
            bot_id=bid,
        )
    else:
        text = ui.error_card("Could not complete rob attempt.", bot_id=bid)

    await message.reply(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /rankings (leaderboard)
# ---------------------------------------------------------------------------

async def _render_leaderboard(
    pool: asyncpg.Pool,
    category: str,
    page: int,
    bot_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    bid = bot_context.resolve_bot_id(bot_id)
    cat = category if category in ("cash", "xp", "gems") else "cash"
    page = max(1, page)
    rows, total_pages = await services.get_leaderboard(pool, cat, page=page, bot_id=bid)
    if page > total_pages:
        page = total_pages
        rows, total_pages = await services.get_leaderboard(pool, cat, page=page, bot_id=bid)

    text = ui.leaderboard_card(category=cat, rows=rows, page=page, total_pages=total_pages, bot_id=bid)
    kb = ui.leaderboard_keyboard(category=cat, page=page, total_pages=total_pages)
    return text, kb


@router.message(Command("rankings"))
async def rankings_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    text, kb = await _render_leaderboard(pool, "cash", page=1, bot_id=bot_ctx.bot_id)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data.startswith("rankings:"))
async def rankings_callback(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    cat = parts[1] if len(parts) > 1 else "cash"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    text, kb = await _render_leaderboard(pool, cat, page=page, bot_id=bot_ctx.bot_id)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    await query.answer()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

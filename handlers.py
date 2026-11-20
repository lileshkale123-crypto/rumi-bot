"""
Telegram command and callback handlers (aiogram 3.x, bot_id & brand aware).
"""

import html
import logging

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

log = logging.getLogger(__name__)
router = Router(name="core_handlers")

PLACEHOLDER_PHOTO_URL = "AgACAgUAAxkBAAIUyGrYXCg69ko8_Zeg5tkBAs4tkxgnAAIdJGsbv2TJVipKp0YrO2yxAQADAgADeQADPQQ"

COMMAND_LIST = [
    ("/wallet", "Check your Wallet"),
    ("/claim", "Claim your daily Reward"),
    ("/guard", "Raise a protective Guard"),
    ("/pay", "Send coins to a friend"),
    ("/rob", "Attempt a heist on someone"),
    ("/spin", "Spin the daily wheel"),
    ("/work", "Work for coins every hour"),
    ("/shop", "Buy titles to show off"),
    ("/q", "Turn a reply into a sticker"),
    ("/qcolors", "See quote colours"),
    ("/mypacks", "See your sticker packs"),
    ("/pact", "Swear a Blood Pact"),
    ("/betray", "Break your pact"),
    ("/bounty", "Put a price on someone"),
    ("/td", "Play Truth or Dare"),
    ("/truth", "Get a truth question"),
    ("/dare", "Get a dare"),
    ("/premium", "Get Premium"),
    ("/setemoji", "Pick your name emoji"),
    ("/referral", "Invite friends, earn coins"),
    ("/help", "Open the full guide"),
]

CATEGORY_META = {
    "cash": {"title": "Richest", "emoji": "💵", "is_currency": True},
    "xp": {"title": "Top XP", "emoji": "⭐", "is_currency": False},
    "gems": {"title": "Most Gems", "emoji": "💎", "is_currency": False},
}


def _get_reply_target(message: Message | None):
    if (
        message
        and message.reply_to_message
        and message.reply_to_message.from_user
        and not message.reply_to_message.from_user.is_bot
    ):
        return message.reply_to_message.from_user
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
        await query.message.reply(ui.games_card(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(F.data == "features")
async def features_callback(query: CallbackQuery) -> None:
    import ax_menu

    await query.answer()
    text, kb = ax_menu.home()
    if query.message:
        await query.message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


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

    prem = await ax_premium.get_premium(pool, target_uid)
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
    if result["status"] == "claimed":
        try:
            import ax_referral

            await ax_referral._pay_on_first_claim(pool, bot, user.id)
        except Exception:
            log.exception("referral first-claim payout failed")

    if target_message is None:
        return

    if result["status"] == "cooldown":
        await target_message.reply(
            ui.reward_cooldown_card(result["next_claim_at"]),
            parse_mode=ParseMode.HTML,
        )
        return
    if result["status"] == "already_claimed_race":
        await target_message.reply(
            ui.error_card("You already claimed your reward just now."),
            parse_mode=ParseMode.HTML,
        )
        return

    await target_message.reply(
        ui.reward_claimed_card(
            result["cash_reward"],
            result["gem_reward"],
            result["lucky_bonus"],
            result["new_streak"],
            result["milestone_hit"],
            result["is_weekly_bonus"],
            result.get("xp"),
        ),
        parse_mode=ParseMode.HTML,
    )


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
# /pay
# ---------------------------------------------------------------------------

async def _respond_to_pay_result(
    message: Message,
    result: dict,
    amount: int,
    target_label: str,
    bot_id: int | None = None,
) -> None:
    brand = get_brand(bot_id)
    status = result["status"]
    if status == "ok":
        text = ui.payment_success_card(
            amount,
            target_label,
            result["new_sender_balance"],
            result["tax"],
            result["received"],
        )
    elif status == "self_transfer":
        text = ui.error_card("You can't pay yourself.")
    elif status == "invalid_amount":
        text = ui.error_card("Enter a valid positive whole amount.")
    elif status == "below_minimum":
        text = ui.error_card("That's below the minimum transfer amount.")
    elif status == "above_maximum":
        text = ui.error_card("That exceeds the maximum transfer amount.")
    elif status == "insufficient_funds":
        text = ui.error_card("You don't have enough balance for this transfer.")
    elif status == "daily_limit_exceeded":
        text = ui.error_card("You've hit your daily transfer limit.")
    elif status == "receiver_not_found":
        text = ui.error_card(f"That user hasn't used {brand.name} yet.")
    else:
        text = ui.error_card("Something went wrong. Please try again.")
    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("pay"))
async def pay_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)

    args = ax_common.cmd_args(message)
    reply_user = _get_reply_target(message)

    if reply_user:
        if len(args) < 1:
            await message.reply(
                ui.error_card("Reply to their message with: /pay amount"),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            amount = int(args[0])
        except ValueError:
            await message.reply(ui.error_card("Enter a valid amount."), parse_mode=ParseMode.HTML)
            return
        await services.ensure_user_and_wallet(
            pool, reply_user.id, reply_user.username, reply_user.first_name, bot_id=bid
        )
        target_label = reply_user.first_name or reply_user.username or "them"
        result = await services.pay_to_id(pool, user.id, reply_user.id, amount, bot_id=bid)
        await _respond_to_pay_result(message, result, amount, target_label, bot_id=bid)
        return

    await message.reply(
        ui.error_card("Reply to their message with: /pay amount"),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /guard (shield)
# ---------------------------------------------------------------------------

@router.message(Command("guard"))
async def guard_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)

    result = await services.apply_ward(pool, user.id, bot_id=bid)
    if result["status"] == "ok":
        text = ui.guard_success_card(result["shield_until"])
    elif result["status"] == "insufficient_funds":
        text = ui.guard_fail_card(result["required"], result["available"])
    elif result["status"] == "already_shielded":
        text = ui.guard_already_active_card(result["shield_until"])
    else:
        text = ui.error_card("Something went wrong. Please try again.")
    await message.reply(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /rob
# ---------------------------------------------------------------------------

@router.message(Command("rob"))
async def rob_command(
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
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)

    reply_user = _get_reply_target(message)
    args = ax_common.cmd_args(message)

    if not reply_user:
        await message.reply(
            ui.error_card("Reply to their message with: /rob amount"),
            parse_mode=ParseMode.HTML,
        )
        return

    amount_arg = args[0] if args else None
    try:
        amount = int(amount_arg)
    except (TypeError, ValueError):
        await message.reply(
            ui.error_card("Reply to their message with: /rob amount"),
            parse_mode=ParseMode.HTML,
        )
        return

    victim_id = reply_user.id
    victim_name = reply_user.first_name or reply_user.username or "them"
    await services.ensure_user_and_wallet(
        pool, victim_id, reply_user.username, reply_user.first_name, bot_id=bid
    )

    result = await services.rob(
        pool,
        user.id,
        victim_id,
        amount,
        chat_id=message.chat.id,
        message_id=message.reply_to_message.message_id,
        bot_id=bid,
    )
    status = result["status"]

    if status == "success":
        await message.reply(
            ui.rob_success_card(result["amount"], victim_name, result["tax"], result["net"])
            + ui.xp_line(result.get("xp")),
            parse_mode=ParseMode.HTML,
        )
        if result.get("bounty"):
            await message.reply(
                "📜 <b>BOUNTY CLAIMED</b>\n<blockquote>🎯 %s collected <b>$%s</b> Coins from the underworld board 😜</blockquote>"
                % (html.escape(user.first_name or "Player"), format(result["bounty"], ",")),
                parse_mode=ParseMode.HTML,
            )
        if result.get("amount", 0) > 2000:
            try:
                rn = html.escape(user.first_name or "Someone")
                cn = html.escape(message.chat.title or "a chat")
                await bot.send_message(
                    victim_id,
                    "\U0001F6A8 <b>You were robbed</b>\n"
                    "<blockquote><b>%s</b> took <b>%d</b> coins from you in %s</blockquote>"
                    % (rn, result["amount"], cn),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
    elif status == "failed":
        await message.reply(
            ui.rob_fail_card(result["penalty"], victim_name) + ui.xp_line(result.get("xp")),
            parse_mode=ParseMode.HTML,
        )
    elif status == "self_rob":
        await message.reply(ui.error_card("You can't rob yourself."), parse_mode=ParseMode.HTML)
    elif status == "cooldown":
        await message.reply(ui.rob_cooldown_card(result["retry_at"]), parse_mode=ParseMode.HTML)
    elif status == "victim_not_found":
        await message.reply(ui.error_card(f"That user hasn't used {brand.name} yet."), parse_mode=ParseMode.HTML)
    elif status == "victim_shielded":
        await message.reply(ui.rob_victim_shielded_card(), parse_mode=ParseMode.HTML)
    elif status == "message_already_robbed":
        await message.reply(
            ui.error_card("That message was already robbed. Reply to a different one."),
            parse_mode=ParseMode.HTML,
        )
    elif status == "victim_too_poor":
        await message.reply(ui.rob_victim_too_poor_card(), parse_mode=ParseMode.HTML)
    elif status == "amount_too_high":
        await message.reply(
            ui.error_card(f"You can rob at most {config.CURRENCY_SYMBOL}{result['max_allowed']} from them right now."),
            parse_mode=ParseMode.HTML,
        )
    elif status == "amount_too_low":
        await message.reply(
            ui.error_card(f"The minimum rob amount is {config.CURRENCY_SYMBOL}{config.ROB_MIN_AMOUNT}."),
            parse_mode=ParseMode.HTML,
        )
    elif status == "invalid_amount":
        await message.reply(ui.error_card("Enter a valid positive whole amount."), parse_mode=ParseMode.HTML)
    else:
        await message.reply(ui.error_card("Something went wrong. Please try again."), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /rankings (leaderboard)
# ---------------------------------------------------------------------------

async def _render_leaderboard(
    pool: asyncpg.Pool,
    category: str,
    page: int,
    bot_id: int | None = None,
):
    bid = bot_context.resolve_bot_id(bot_id)
    rows, total = await services.get_leaderboard(
        pool, category, page, config.LEADERBOARD_PAGE_SIZE, bot_id=bid
    )
    total_pages = max(1, -(-total // config.LEADERBOARD_PAGE_SIZE))
    page = min(max(1, page), total_pages)
    meta = CATEGORY_META[category]
    entries = [{"name": r["name"], "value": r["value"], "uid": r["uid"]} for r in rows]

    if entries:
        try:
            import ax_shop

            uids = [e["uid"] for e in entries]
            title_rows = await pool.fetch(
                "SELECT user_id, item_id FROM ax_shop_owned WHERE user_id = ANY($1::bigint[]) AND equipped",
                uids,
            )
            title_map = {}
            for tr in title_rows:
                item = ax_shop.ITEMS.get(tr["item_id"])
                if item:
                    title_map[tr["user_id"]] = f"{item[0]} {item[1]}"
            for e in entries:
                e["title"] = title_map.get(e["uid"])
        except Exception:
            pass

    text = ui.leaderboard_card(meta["title"], meta["emoji"], entries, page, total_pages, meta["is_currency"])
    keyboard = ui.leaderboard_keyboard(category, page, total_pages)
    return text, keyboard


@router.message(Command("rankings"))
async def rankings_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    text, keyboard = await _render_leaderboard(pool, "cash", 1, bot_id=bot_ctx.bot_id)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data.startswith("rankings:"))
async def rankings_callback(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("cash", "xp", "gems") or not parts[2].isdigit():
        await query.answer()
        return

    category = parts[1]
    page = max(1, int(parts[2]))
    text, keyboard = await _render_leaderboard(pool, category, page, bot_id=bot_ctx.bot_id)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

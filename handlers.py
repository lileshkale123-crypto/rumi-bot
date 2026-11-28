"""Core handlers: /start, /wallet, /claim, /pay, /guard, /rob, /rankings (aiogram 3.x, bot_id aware).
Rebuilt to match the current services.py / ui.py signatures."""
import html
import logging
import os
from collections import OrderedDict

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message

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
CATEGORY_META = {
    "cash": ("Richest", "💵", True),
    "xp": ("Top XP", "⭐", False),
    "gems": ("Most Gems", "💎", False),
}
S = config.CURRENCY_SYMBOL


def _get_reply_target(message: Message | None):
    if message and message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if not target.is_bot:
            return target
    return None


def _parse_amount(args) -> int | None:
    if not args:
        return None
    raw = args[0].replace(",", "").lstrip(S)
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


# ---------------------------------------------------------------- /start
_START_IMG_PATH = "assets/rumi-start-v2.png"
_START_FILE_IDS: "OrderedDict[int, str]" = OrderedDict()  # bot_id -> Telegram file_id (ids differ per bot)
_START_FILE_IDS_MAX = 50


_CUSTOM_START_PIC: dict[int, str | None] = {}  # bot_id -> file_id set from /ownerpanel (None = use default)


def set_custom_start_pic(bot_id: int, file_id: str | None) -> None:
    """Called by the owner panel after the start picture is changed or reset."""
    _CUSTOM_START_PIC[bot_id] = file_id
    _START_FILE_IDS.pop(bot_id, None)


async def _get_custom_start_pic(pool, bot_id: int) -> str | None:
    if bot_id in _CUSTOM_START_PIC:
        return _CUSTOM_START_PIC[bot_id]
    val = None
    try:
        async with pool.acquire() as c:
            val = await c.fetchval("SELECT value FROM ax_owner_settings WHERE key=$1", f"start_pic:{bot_id}")
    except Exception:
        logger.warning("could not read custom start pic", exc_info=True)
        return None
    _CUSTOM_START_PIC[bot_id] = val or None
    return _CUSTOM_START_PIC[bot_id]


async def _send_start_card(message: Message, bot_id: int, text: str, keyboard, pool=None) -> None:
    """Picture first (owner's custom pic, cached file_id, local file, web link), plain text last."""
    custom = await _get_custom_start_pic(pool, bot_id) if pool is not None else None
    if custom:
        try:
            await message.reply_photo(photo=custom, caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return
        except TelegramBadRequest as e:
            logger.warning("custom start photo failed, using default: %s", str(e)[:120])
    cached = _START_FILE_IDS.get(bot_id)
    sources: list = []
    if cached:
        sources.append(cached)
    if os.path.isfile(_START_IMG_PATH):
        sources.append(FSInputFile(_START_IMG_PATH))
    sources.append(PLACEHOLDER_PHOTO_URL)
    for source in sources:
        try:
            sent = await message.reply_photo(
                photo=source,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except TelegramBadRequest as e:
            logger.warning("start photo failed (%s): %s", type(source).__name__, str(e)[:120])
            if cached and source is cached:
                _START_FILE_IDS.pop(bot_id, None)
            continue
        if sent.photo:
            _START_FILE_IDS[bot_id] = sent.photo[-1].file_id
            _START_FILE_IDS.move_to_end(bot_id)
            while len(_START_FILE_IDS) > _START_FILE_IDS_MAX:
                _START_FILE_IDS.popitem(last=False)
        return
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.message(CommandStart())
async def start_command(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    brand = get_brand(bid)
    uname = bot_ctx.username or await ax_common.get_bot_username(bot)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    wallet = await services.get_wallet(pool, user.id, bot_id=bid)
    if message.chat.type in ("group", "supergroup"):
        fn = html.escape(user.first_name or "friend")
        bname = html.escape(brand.name)
        short_kb = InlineKeyboardMarkup(inline_keyboard=[[
            ax_common.btn(f"\U0001F49C Start {brand.name} in DM", url=f"https://t.me/{uname}", style="primary")
        ]])
        await message.reply(
            f"<b>Hey {fn}</b> \U0001F49C\n"
            f"<blockquote>I'm {bname}. Tap below to start me in DM for your wallet, games and the full guide \u2728</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=short_kb,
        )
        return
    text = ui.start_card(user.first_name or user.username or "Traveler", wallet, COMMAND_LIST, bot_id=bid)
    kb = ui.start_keyboard(uname, bot_id=bid)
    await _send_start_card(message, bid, text, kb, pool=pool)


@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery) -> None:
    await query.answer()


# ---------------------------------------------------------------- /wallet
async def _build_wallet_view(pool, user, reply_target=None, bot_id=None):
    bid = bot_context.resolve_bot_id(bot_id)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    other = bool(reply_target and reply_target.id != user.id)
    if other:
        await services.ensure_user_and_wallet(pool, reply_target.id, reply_target.username, reply_target.first_name, bot_id=bid)
    target = reply_target if other else user
    name = target.first_name or target.username or "Traveler"
    wallet = await services.get_wallet(pool, target.id, bot_id=bid)
    text = ui.wallet_card(name, wallet, full=other, bot_id=bid)
    kb = None if other else ui.wallet_keyboard()
    try:
        import ax_premium
        prem = await ax_premium.get_premium(pool, target.id, bot_id=bid)
        if prem is not None:
            text = ui.wallet_card_premium(name, wallet, ax_premium.emoji_html(prem), full=other, bot_id=bid)
            kb = None
    except Exception:
        logger.exception("premium wallet view failed")
    return text, kb


@router.message(Command("wallet"))
async def wallet_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user is None:
        return
    text, kb = await _build_wallet_view(pool, message.from_user, _get_reply_target(message), bot_id=bot_ctx.bot_id)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data == "wallet:refresh")
async def wallet_refresh_callback(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    text, kb = await _build_wallet_view(pool, query.from_user, None, bot_id=bot_ctx.bot_id)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    await query.answer()


# ---------------------------------------------------------------- /claim
async def _execute_claim(user, target_message, bot: Bot, pool: asyncpg.Pool, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    result = await services.claim_daily(pool, user.id, bot_id=bid)
    status = result["status"]
    if status == "claimed":
        text = ui.reward_claimed_card(
            result["cash_reward"], result["gem_reward"], result["lucky_bonus"], result["new_streak"],
            result["milestone_hit"], result["is_weekly_bonus"], result.get("xp"),
        )
        try:
            import ax_referral
            ax_common.fire(ax_referral._pay_on_first_claim(pool, bot, user.id, bot_id=bid))
        except Exception:
            pass
    elif status == "cooldown":
        text = ui.reward_cooldown_card(result["next_claim_at"])
    elif status == "already_claimed_race":
        text = ui.error_card("You already claimed today. Come back tomorrow!")
    else:
        text = ui.error_card("Could not process claim right now. Please try again.")
    if target_message:
        await target_message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("claim"))
async def claim_command(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user:
        await _execute_claim(message.from_user, message, bot, pool, bot_id=bot_ctx.bot_id)


@router.callback_query(F.data == "claim")
async def claim_callback(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await query.answer()
    await _execute_claim(query.from_user, query.message, bot, pool, bot_id=bot_ctx.bot_id)


# ---------------------------------------------------------------- /pay
@router.message(Command("pay"))
async def pay_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    sender = message.from_user
    if sender is None:
        return
    bid = bot_ctx.bot_id
    target = _get_reply_target(message)
    amount = _parse_amount(ax_common.cmd_args(message))
    if target is None or amount is None:
        await message.reply(ui.error_card("Reply to a user's message with /pay &lt;amount&gt;. Example: /pay 250"), parse_mode=ParseMode.HTML)
        return
    await services.ensure_user_and_wallet(pool, sender.id, sender.username, sender.first_name, bot_id=bid)
    await services.ensure_user_and_wallet(pool, target.id, target.username, target.first_name, bot_id=bid)
    r = await services.pay_to_id(pool, sender.id, target.id, amount, bot_id=bid)
    st = r["status"]
    if st == "ok":
        text = ui.payment_success_card(amount, target.first_name or target.username or "User", r["new_sender_balance"], r["tax"], r["received"])
    elif st == "self_transfer":
        text = ui.error_card("You cannot pay yourself.")
    elif st == "below_minimum":
        text = ui.error_card(f"Minimum transfer is {S}{config.MIN_TRANSFER}.")
    elif st == "above_maximum":
        text = ui.error_card(f"Maximum single transfer is {S}{config.MAX_TRANSFER:,}.")
    elif st == "insufficient_funds":
        text = ui.error_card("Insufficient funds.")
    elif st == "daily_limit_exceeded":
        text = ui.error_card(f"Daily transfer limit reached ({S}{config.DAILY_TRANSFER_LIMIT:,}/day).")
    elif st == "receiver_not_found":
        text = ui.error_card("That user has no wallet yet.")
    else:
        text = ui.error_card("Could not complete transfer.")
    await message.reply(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------- /guard
@router.message(Command("guard"))
async def guard_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    r = await services.apply_ward(pool, user.id, bot_id=bid)
    st = r["status"]
    if st == "ok":
        text = ui.guard_success_card(r["shield_until"])
    elif st == "already_shielded":
        text = ui.guard_already_active_card(r["shield_until"])
    elif st == "insufficient_funds":
        text = ui.guard_fail_card(r["required"], r["available"])
    else:
        text = ui.error_card("Could not activate guard right now.")
    await message.reply(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------- /rob
@router.message(Command("rob"))
async def rob_command(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    robber = message.from_user
    if robber is None:
        return
    bid = bot_ctx.bot_id
    victim = _get_reply_target(message)
    if victim is None:
        await message.reply(ui.error_card("Reply to a user's message with /rob &lt;amount&gt;. Example: /rob 500"), parse_mode=ParseMode.HTML)
        return
    amount = _parse_amount(ax_common.cmd_args(message))
    if amount is None:
        await message.reply(ui.error_card("Enter a valid positive amount. Example: /rob 500"), parse_mode=ParseMode.HTML)
        return
    await services.ensure_user_and_wallet(pool, robber.id, robber.username, robber.first_name, bot_id=bid)
    await services.ensure_user_and_wallet(pool, victim.id, victim.username, victim.first_name, bot_id=bid)
    r = await services.rob(pool, robber.id, victim.id, amount, chat_id=message.chat.id, message_id=message.message_id, bot_id=bid)
    st = r["status"]
    vname = victim.first_name or victim.username or "User"
    if st == "success":
        try:
            import ax_tasks
            ax_common.fire(ax_tasks.record_task_action(pool, robber.id, "robs", bot_id=bid))
        except Exception:
            pass
    if st == "success":
        text = ui.rob_success_card(r["amount"], vname, r["tax"], r["net"])
        if r.get("xp"):
            text += ui.xp_line(r["xp"])
    elif st == "failed":
        text = ui.rob_fail_card(r["penalty"], vname)
    elif st == "cooldown":
        text = ui.rob_cooldown_card(r["retry_at"])
    elif st == "victim_shielded":
        text = ui.rob_victim_shielded_card()
    elif st == "victim_too_poor":
        text = ui.rob_victim_too_poor_card()
    elif st == "self_rob":
        text = ui.error_card("You cannot rob yourself.")
    elif st == "amount_too_low":
        text = ui.error_card(f"Minimum rob amount is {S}{config.ROB_MIN_AMOUNT:,}.")
    elif st == "amount_too_high":
        text = ui.error_card(f"You can rob at most {S}{r['max_allowed']:,} from this user.")
    elif st == "daily_rob_limit":
        text = ui.error_card(f"Daily rob limit reached ({r['used']}/{r['cap']}). Try again tomorrow.")
    elif st == "message_already_robbed":
        text = ui.error_card("Someone already robbed this message while the victim was low on cash.")
    elif st == "victim_not_found":
        text = ui.error_card("That user has no wallet yet.")
    else:
        text = ui.error_card("Could not complete rob attempt.")
    await message.reply(text, parse_mode=ParseMode.HTML)
    if st == "success":
        rname = html.escape(robber.first_name or "Someone")
        if r.get("bounty"):
            try:
                await message.reply(
                    "\U0001F4DC <b>BOUNTY CLAIMED</b>\n<blockquote>\U0001F3AF %s collected <b>%s%s</b> Coins from the underworld board \U0001F61C</blockquote>"
                    % (rname, S, format(int(r["bounty"]), ",")),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        if int(r.get("amount") or 0) > 2000:
            try:
                cname = html.escape(message.chat.title or "a chat")
                await bot.send_message(
                    victim.id,
                    "\U0001F6A8 <b>You were robbed</b>\n"
                    "<blockquote><b>%s</b> took <b>%s%s</b> from you in %s</blockquote>"
                    % (rname, S, format(int(r["amount"]), ","), cname),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass  # victim never started the bot in DM, or blocked it


# ---------------------------------------------------------------- /rankings
async def _render_leaderboard(pool, category: str, page: int, bot_id=None):
    bid = bot_context.resolve_bot_id(bot_id)
    cat = category if category in CATEGORY_META else "cash"
    size = config.LEADERBOARD_PAGE_SIZE
    page = max(1, page)
    rows, total = await services.get_leaderboard(pool, cat, page, size, bot_id=bid)
    total_pages = max(1, (int(total) + size - 1) // size)
    if page > total_pages:
        page = total_pages
        rows, total = await services.get_leaderboard(pool, cat, page, size, bot_id=bid)
    title, emoji, is_cur = CATEGORY_META[cat]
    entries = [{"name": r["name"], "value": r["value"]} for r in rows]
    text = ui.leaderboard_card(title, emoji, entries, page, total_pages, is_cur)
    return text, ui.leaderboard_keyboard(cat, page, total_pages)


@router.message(Command("rankings"))
async def rankings_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    text, kb = await _render_leaderboard(pool, "cash", 1, bot_id=bot_ctx.bot_id)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data.startswith("rankings:"))
async def rankings_callback(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    cat = parts[1] if len(parts) > 1 else "cash"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    text, kb = await _render_leaderboard(pool, cat, page, bot_id=bot_ctx.bot_id)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    await query.answer()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

# --- start photo patch 2 applied ---

import html
import logging
import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
import ax_common, bot_context, config, services, ui
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="core_handlers")
PLACEHOLDER_PHOTO_URL = "AgACAgUAAxkBAAIUyGrYXCg69ko8_Zeg5tkBAs4tkxgnAAIdJGsbv2TJVipKp0YrO2yxAQADAgADeQADPQQ"
COMMAND_LIST = [("/wallet", "Wallet"), ("/claim", "Claim")]
CATEGORY_META = {"cash": {"title": "Richest", "emoji": "💵", "is_currency": True}, "xp": {"title": "Top XP", "emoji": "⭐", "is_currency": False}, "gems": {"title": "Most Gems", "emoji": "💎", "is_currency": False}}

def _get_reply_target(message: Message | None):
    if message and message.reply_to_message and message.reply_to_message.from_user and not message.reply_to_message.from_user.is_bot:
        return message.reply_to_message.from_user
    return None

@router.message(CommandStart())
async def start_command(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user is None:
        return
    await services.ensure_user_and_wallet(pool, message.from_user.id, message.from_user.username, message.from_user.first_name, bot_id=bot_ctx.bot_id)
    w = await services.get_wallet(pool, message.from_user.id, bot_id=bot_ctx.bot_id)
    await message.reply(ui.start_card(message.from_user.first_name or "Player", w, COMMAND_LIST, bot_id=bot_ctx.bot_id), parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "games")
async def games_callback(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.reply(ui.games_card(), parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery) -> None:
    await query.answer()

async def _build_wallet_view(pool: asyncpg.Pool, user, reply_target=None, bot_id: int | None = None):
    bid = bot_context.resolve_bot_id(bot_id)
    target_uid = reply_target.id if reply_target else user.id
    name = (reply_target.first_name if reply_target else user.first_name) or "Player"
    wallet = await services.get_wallet(pool, target_uid, bot_id=bid)
    text = ui.wallet_card(name, wallet, full=bool(reply_target), bot_id=bid)
    kb = None if reply_target else ui.wallet_keyboard()
    import ax_premium
    prem = await ax_premium.get_premium(pool, target_uid, bot_id=bid)
    if prem is not None:
        text = ui.wallet_card_premium(name, wallet, ax_premium.emoji_html(prem), full=bool(reply_target), bot_id=bid)
        kb = None
    return text, kb

@router.message(Command("wallet"))
async def wallet_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user:
        t, k = await _build_wallet_view(pool, message.from_user, _get_reply_target(message), bot_id=bot_ctx.bot_id)
        await message.reply(t, parse_mode=ParseMode.HTML, reply_markup=k)

@router.message(Command("claim"))
async def claim_command(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user:
        r = await services.claim_daily(pool, message.from_user.id, bot_id=bot_ctx.bot_id)
        await message.reply(ui.reward_claimed_card(r["cash_reward"], r["gem_reward"], r["lucky_bonus"], r["new_streak"], r["milestone_hit"], r["is_weekly_bonus"], r.get("xp")), parse_mode=ParseMode.HTML)

@router.message(Command("pay"))
async def pay_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    rp = _get_reply_target(message)
    args = ax_common.cmd_args(message)
    if message.from_user and rp and args and args[0].isdigit():
        r = await services.pay_to_id(pool, message.from_user.id, rp.id, int(args[0]), bot_id=bot_ctx.bot_id)
        await message.reply(ui.payment_success_card(int(args[0]), rp.first_name or "User", r.get("new_sender_balance", 0), r.get("tax", 0), r.get("received", 0)), parse_mode=ParseMode.HTML)

@router.message(Command("guard"))
async def guard_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user:
        r = await services.apply_ward(pool, message.from_user.id, bot_id=bot_ctx.bot_id)
        if r["status"] == "ok":
            await message.reply(ui.guard_success_card(r["shield_until"]), parse_mode=ParseMode.HTML)

@router.message(Command("rob"))
async def rob_command(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    rp = _get_reply_target(message)
    args = ax_common.cmd_args(message)
    if message.from_user and rp and args and args[0].isdigit():
        r = await services.rob(pool, message.from_user.id, rp.id, int(args[0]), bot_id=bot_ctx.bot_id)
        if r["status"] == "success":
            await message.reply(ui.rob_success_card(r["amount"], rp.first_name or "User", r["tax"], r["net"]), parse_mode=ParseMode.HTML)

@router.message(Command("rankings"))
async def rankings_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await message.reply(ui.leaderboard_card("Richest", "💵", [], 1, 1, True), parse_mode=ParseMode.HTML)

def register(dp):
    if router.parent_router is None:
        dp.include_router(router)

"""Stars donations: /donate buttons, payment records, owner donors page (aiogram 3.x, bot_id & brand aware)."""
import logging

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery

import bot_context
from ax_common import btn, esc
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="ax_donate")

H = ParseMode.HTML
NOTIFY_ID = 8151412244
AMOUNTS = (5, 10)
PER = 8
_READY = [False]


async def ensure(pool: asyncpg.Pool) -> None:
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_donations ("
            "id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, "
            "first_name TEXT, stars INT NOT NULL, charge_id TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "bot_id BIGINT NOT NULL DEFAULT 0)"
        )
    _READY[0] = True


@router.message(Command("donate"))
async def donate_cmd(message: Message, bot_ctx: BotContext) -> None:
    bname = esc(get_brand(bot_ctx.bot_id).name)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn("⭐ 5 Stars", "axd:5", style="primary"),
                btn("🌟 10 Stars", "axd:10", style="success"),
            ]
        ]
    )
    await message.reply(
        f"💖 <b>Support {bname}</b>\n"
        f"<blockquote>{bname} is free to use ✨ Gifts keep her running.</blockquote>\n\n"
        f"Pick a gift below. Sharing {bname} with friends helps too.",
        parse_mode=H,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("axd:"))
async def pick(query: CallbackQuery, bot: Bot, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    await query.answer()
    if len(parts) != 2 or not parts[1].isdigit() or query.message is None:
        return
    n = int(parts[1])
    if n not in AMOUNTS:
        return
    bname = get_brand(bot_ctx.bot_id).name
    await bot.send_invoice(
        chat_id=query.message.chat.id,
        title=f"Support {bname}",
        description=f"Thank you for helping {bname} grow 💖",
        payload="donate:%d" % n,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Donation", amount=n)],
    )


@router.pre_checkout_query()
async def precheckout(query: PreCheckoutQuery) -> None:
    ok = (query.invoice_payload or "").startswith(("donate:", "premium:"))
    await query.answer(ok=ok, error_message=None if ok else "Unknown payment")


@router.message(F.successful_payment.invoice_payload.startswith("donate:"))
async def paid(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    sp = message.successful_payment
    user = message.from_user
    if not sp or user is None:
        return
    bid = bot_ctx.bot_id
    bname = esc(get_brand(bid).name)
    name = user.first_name or "Friend"
    try:
        await ensure(pool)
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_donations (bot_id, user_id, first_name, stars, charge_id) "
                "VALUES ($1,$2,$3,$4,$5)",
                bid,
                user.id,
                name,
                sp.total_amount,
                sp.telegram_payment_charge_id,
            )
    except Exception:
        log.exception("DONATION save failed")

    await message.reply(
        f"💖 <b>Thank you, %s</b>\n<blockquote>Your %d ⭐ help {bname} grow ✨</blockquote>"
        % (esc(name), sp.total_amount),
        parse_mode=H,
    )
    try:
        await bot.send_message(
            NOTIFY_ID,
            "⭐ <b>New donation</b>\n<blockquote>%s gave %d Stars</blockquote>"
            % (esc(name), sp.total_amount),
            parse_mode=H,
        )
    except Exception:
        pass


async def donors_page(
    pool: asyncpg.Pool,
    page: int,
    bot_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    bid = bot_context.resolve_bot_id(bot_id)
    await ensure(pool)
    async with pool.acquire() as c:
        tot = await c.fetchrow(
            "SELECT COUNT(*) AS n, COALESCE(SUM(stars),0) AS s FROM ax_donations WHERE bot_id=$1",
            bid,
        )
        pages = max(1, -(-tot["n"] // PER))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT user_id, first_name, stars, created_at FROM ax_donations "
            "WHERE bot_id=$1 ORDER BY id DESC LIMIT $2 OFFSET $3",
            bid,
            PER,
            (page - 1) * PER,
        )
    lines = []
    for r in rows:
        lines.append(
            f"⭐ <b>{esc(r['first_name'] or 'Friend')}</b> • <code>{r['stars']}</code> Stars\n"
            f"      <code>{r['user_id']}</code> • {r['created_at']:%d %b %Y}"
        )
    body = "\n".join(lines) if lines else "No donations yet."
    text = (
        f"⭐ <b>DONORS</b>\n<i>{tot['n']} gifts • {tot['s']} Stars total</i>\n\n"
        f"<blockquote>{body}</blockquote>\n\n<i>page {page}/{pages}</i>"
    )
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axp:donors:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axp:donors:{page + 1}"))
    return text, InlineKeyboardMarkup(inline_keyboard=[nav, [btn("🏠 Groups", "axp:cash:1")]])


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

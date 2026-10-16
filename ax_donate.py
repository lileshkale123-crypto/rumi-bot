"""Stars donations: /donate buttons, payment records, owner donors page."""
import logging

from telegram import InlineKeyboardMarkup, LabeledPrice
from telegram.constants import ParseMode
from telegram.ext import (CallbackQueryHandler, CommandHandler,
                          MessageHandler, PreCheckoutQueryHandler, filters)

from ax_common import btn, esc

H = ParseMode.HTML
NOTIFY_ID = 8151412244
AMOUNTS = (5, 10)
PER = 8
_READY = [False]


async def ensure(pool):
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_donations ("
            "id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, "
            "first_name TEXT, stars INT NOT NULL, charge_id TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    _READY[0] = True


async def donate_cmd(update, context):
    kb = InlineKeyboardMarkup([[
        btn("⭐ 5 Stars", "axd:5", style="primary"),
        btn("🌟 10 Stars", "axd:10", style="success")]])
    await update.effective_message.reply_text(
        "💖 <b>Support Rumi</b>\n"
        "<blockquote>Rumi is free to use ✨ Gifts keep her running.</blockquote>\n\n"
        "Pick a gift below. Sharing Rumi with friends helps too.",
        parse_mode=H, reply_markup=kb)


async def pick(update, context):
    q = update.callback_query
    n = int(q.data.split(":")[1])
    await q.answer()
    if n not in AMOUNTS:
        return
    await context.bot.send_invoice(
        chat_id=q.message.chat_id, title="Support Rumi",
        description="Thank you for helping Rumi grow 💖",
        payload="donate:%d" % n, provider_token="", currency="XTR",
        prices=[LabeledPrice("Donation", n)])


async def precheckout(update, context):
    q = update.pre_checkout_query
    ok = q.invoice_payload.startswith("donate:")
    await q.answer(ok=ok, error_message=None if ok else "Unknown payment")


async def paid(update, context):
    sp = update.effective_message.successful_payment
    user = update.effective_user
    if not sp or not sp.invoice_payload.startswith("donate:"):
        return
    name = user.first_name or "Friend"
    try:
        pool = context.bot_data["pool"]
        await ensure(pool)
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_donations (user_id, first_name, stars, charge_id) "
                "VALUES ($1,$2,$3,$4)",
                user.id, name, sp.total_amount, sp.telegram_payment_charge_id)
    except Exception:
        logging.exception("DONATION save failed")
    await update.effective_message.reply_text(
        "💖 <b>Thank you, %s</b>\n<blockquote>Your %d ⭐ help Rumi grow ✨</blockquote>"
        % (esc(name), sp.total_amount), parse_mode=H)
    try:
        await context.bot.send_message(
            NOTIFY_ID, "⭐ <b>New donation</b>\n<blockquote>%s gave %d Stars</blockquote>"
            % (esc(name), sp.total_amount), parse_mode=H)
    except Exception:
        pass


async def donors_page(pool, page):
    await ensure(pool)
    async with pool.acquire() as c:
        tot = await c.fetchrow(
            "SELECT COUNT(*) AS n, COALESCE(SUM(stars),0) AS s FROM ax_donations")
        pages = max(1, -(-tot["n"] // PER))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT user_id, first_name, stars, created_at FROM ax_donations "
            "ORDER BY id DESC LIMIT $1 OFFSET $2", PER, (page - 1) * PER)
    lines = []
    for r in rows:
        lines.append(
            f"⭐ <b>{esc(r['first_name'] or 'Friend')}</b> • <code>{r['stars']}</code> Stars\n"
            f"      <code>{r['user_id']}</code> • {r['created_at']:%d %b %Y}")
    body = "\n".join(lines) if lines else "No donations yet."
    text = (f"⭐ <b>DONORS</b>\n<i>{tot['n']} gifts • {tot['s']} Stars total</i>\n\n"
            f"<blockquote>{body}</blockquote>\n\n<i>page {page}/{pages}</i>")
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axp:donors:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axp:donors:{page + 1}"))
    return text, InlineKeyboardMarkup([nav, [btn("🏠 Groups", "axp:cash:1")]])


def register(app):
    app.add_handler(CommandHandler("donate", donate_cmd))
    app.add_handler(CallbackQueryHandler(pick, pattern=r"^axd:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid), group=5)

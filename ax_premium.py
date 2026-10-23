"""Rumi Premium: 35 Stars, owner grant, wallet emoji."""
import logging
from telegram import LabeledPrice
from telegram.constants import ParseMode
from telegram.ext import (CallbackQueryHandler, CommandHandler,
                          MessageHandler, filters)

from ax_adminpanel import OWNER_IDS
from ax_common import btn, esc

H = ParseMode.HTML
PRICE = 35
_READY = [False]


async def ensure(pool):
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_premium ("
            "user_id BIGINT PRIMARY KEY, emoji_id TEXT, emoji_char TEXT, "
            "since TIMESTAMPTZ NOT NULL DEFAULT now())")
    _READY[0] = True


async def get_premium(pool, uid):
    await ensure(pool)
    async with pool.acquire() as c:
        return await c.fetchrow(
            "SELECT emoji_id, emoji_char FROM ax_premium WHERE user_id=$1", uid)


def emoji_html(row):
    if not row or not row["emoji_char"]:
        return ""
    ch = esc(row["emoji_char"])
    if row["emoji_id"]:
        return '<tg-emoji emoji-id="%s">%s</tg-emoji> ' % (row["emoji_id"], ch)
    return ch + " "


async def _grant(pool, uid):
    await ensure(pool)
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_premium (user_id) VALUES ($1) "
            "ON CONFLICT (user_id) DO NOTHING", uid)


def _target(update, context):
    m = update.effective_message
    if m.reply_to_message and m.reply_to_message.from_user:
        u = m.reply_to_message.from_user
        return u.id, u.first_name or "Friend"
    if context.args and context.args[0].lstrip("-").isdigit():
        return int(context.args[0]), context.args[0]
    return None, None


async def premium_cmd(update, context):
    pool = context.bot_data["pool"]
    row = await get_premium(pool, update.effective_user.id)
    if row is not None:
        await update.effective_message.reply_text(
            "💎 <b>You are Rumi Premium</b>\n<blockquote>Reply to an emoji "
            "with /setemoji to wear it before your name.</blockquote>",
            parse_mode=H)
        return
    kb = [[btn("⭐ Get Premium - %d Stars" % PRICE, "axp:buy", style="success")]]
    from telegram import InlineKeyboardMarkup
    await update.effective_message.reply_text(
        "💎 <b>Rumi Premium</b>\n<blockquote>A bigger, cleaner wallet card "
        "and your own emoji before your name.</blockquote>",
        parse_mode=H, reply_markup=InlineKeyboardMarkup(kb))


async def buy(update, context):
    q = update.callback_query
    await q.answer()
    await context.bot.send_invoice(
        chat_id=q.message.chat_id, title="Rumi Premium",
        description="Premium wallet and name emoji",
        payload="premium:%d" % q.from_user.id, provider_token="",
        currency="XTR", prices=[LabeledPrice("Rumi Premium", PRICE)])


async def paid(update, context):
    sp = update.effective_message.successful_payment
    user = update.effective_user
    if not sp or not sp.invoice_payload.startswith("premium:"):
        return
    try:
        await _grant(context.bot_data["pool"], user.id)
    except Exception:
        logging.exception("PREMIUM save failed")
    await update.effective_message.reply_text(
        "💎 <b>Welcome to Premium, %s</b>\n<blockquote>Try /wallet, and reply "
        "to an emoji with /setemoji.</blockquote>" % esc(user.first_name or "Friend"),
        parse_mode=H)


async def givepremium(update, context):
    if update.effective_user.id not in OWNER_IDS:
        return
    uid, name = _target(update, context)
    if uid is None:
        await update.effective_message.reply_text("Use: /givepremium USER_ID (or reply)")
        return
    await _grant(context.bot_data["pool"], uid)
    await update.effective_message.reply_text(
        "💎 <b>%s</b> is now Premium" % esc(name), parse_mode=H)


async def removepremium(update, context):
    if update.effective_user.id not in OWNER_IDS:
        return
    uid, name = _target(update, context)
    if uid is None:
        await update.effective_message.reply_text("Use: /removepremium USER_ID (or reply)")
        return
    pool = context.bot_data["pool"]
    await ensure(pool)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_premium WHERE user_id=$1", uid)
    await update.effective_message.reply_text("Removed Premium from %s" % esc(name), parse_mode=H)


async def setemoji(update, context):
    user = update.effective_user
    pool = context.bot_data["pool"]
    if await get_premium(pool, user.id) is None and user.id not in OWNER_IDS:
        await update.effective_message.reply_text("💎 This is a Premium feature. Try /premium")
        return
    m = update.effective_message
    r = m.reply_to_message
    own = False
    if r is None or not (r.text or r.caption):
        r = m
        own = True
    text = r.text or r.caption or ""
    ents = r.entities or r.caption_entities or []
    eid, ch = None, None
    for e in ents:
        if e.type == "custom_emoji":
            raw = text.encode("utf-16-le")
            ch = raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
            eid = e.custom_emoji_id
            break
    _t = text.strip()
    if own and _t.startswith("/"):
        _p = _t.split(None, 1)
        _t = _p[1].strip() if len(_p) > 1 else ""
    if eid is None and _t and len(_t) <= 8:
        ch = _t
    if not ch:
        await update.effective_message.reply_text("Reply to a message that has only an emoji, then send /setemoji")
        return
    await _grant(pool, user.id) if user.id in OWNER_IDS else None
    async with pool.acquire() as c:
        await c.execute("UPDATE ax_premium SET emoji_id=$2, emoji_char=$3 WHERE user_id=$1", user.id, eid, ch)
    _n = "premium emoji ID found" if eid else "normal emoji only, no premium ID"
    await update.effective_message.reply_text("✨ Emoji saved (%s). Check /wallet" % _n)


def register(app):
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("givepremium", givepremium))
    app.add_handler(CommandHandler("removepremium", removepremium))
    app.add_handler(CommandHandler("setemoji", setemoji))
    app.add_handler(CallbackQueryHandler(buy, pattern=r"^axp:buy"))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid), group=6)

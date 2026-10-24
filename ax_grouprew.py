"""20k cash for adding Rumi to a group with 50+ members (once per group)."""
import html
import logging

from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import ChatMemberHandler

import services
from ax_referral import _credit

H = ParseMode.HTML
REWARD = 20000
MIN_MEMBERS = 50
_READY = [False]


async def _ensure(pool):
    if _READY[0]:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_group_rewards ("
            "chat_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    _READY[0] = True


async def on_added(update, context):
    r = update.my_chat_member
    if r is None or r.chat.type not in ("group", "supergroup"):
        return
    old = r.old_chat_member.status
    new = r.new_chat_member.status
    if old not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        return
    if new not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return
    adder = r.from_user
    if adder is None or adder.is_bot:
        return
    try:
        count = await context.bot.get_chat_member_count(r.chat.id)
    except Exception:
        return
    if count < MIN_MEMBERS:
        return
    pool = context.bot_data["pool"]
    try:
        await _ensure(pool)
        await services.ensure_user_and_wallet(
            pool, adder.id, adder.username, adder.first_name)
        async with pool.acquire() as c:
            async with c.transaction():
                ins = await c.fetchval(
                    "INSERT INTO ax_group_rewards (chat_id, user_id) VALUES ($1,$2) "
                    "ON CONFLICT DO NOTHING RETURNING chat_id", r.chat.id, adder.id)
                if ins is None:
                    return
                if not await _credit(c, adder.id, REWARD, "group_add_reward"):
                    raise RuntimeError("adder has no wallet")
    except Exception:
        logging.exception("group add reward failed")
        return
    try:
        await context.bot.send_message(
            r.chat.id,
            "🎁 <b>Thank you, %s</b>\n<blockquote>You added Rumi to a group with "
            "%d members and earned <code>20,000</code> cash 💖</blockquote>"
            % (html.escape(adder.first_name or "Friend"), count),
            parse_mode=H)
    except Exception:
        pass


def register(app):
    app.add_handler(
        ChatMemberHandler(on_added, ChatMemberHandler.MY_CHAT_MEMBER), group=-3)

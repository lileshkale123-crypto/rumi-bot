"""Shared helpers for the Astraea-style features in Rumi."""
import html
from types import SimpleNamespace

from telegram import InlineKeyboardButton
from telegram.constants import ChatMemberStatus

ADMIN_STATES = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


def esc(text) -> str:
    return html.escape(str(text))


def btn(text, data=None, url=None, style=None):
    """style: 'success' green, 'danger' red, 'primary' blue, None normal."""
    kw = {"api_kwargs": {"style": style}} if style else {}
    if url:
        return InlineKeyboardButton(text, url=url, **kw)
    return InlineKeyboardButton(text, callback_data=data, **kw)


async def is_admin(bot, chat_id, user_id) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ADMIN_STATES
    except Exception:
        return False


async def bot_is_admin(bot, chat_id) -> bool:
    return await is_admin(bot, chat_id, bot.id)


async def find_user_by_username(pool, username):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, first_name FROM users WHERE username ILIKE $1", username
        )


async def resolve_target(update, context, pool):
    """Reply, tap-mention, numeric id or @username. Returns an object
    with .id and .first_name, or None."""
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user
    for ent in msg.entities or []:
        if ent.type == "text_mention" and ent.user:
            return ent.user
    args = context.args or []
    if not args:
        return None
    ident = args[0]
    if ident.lstrip("-").isdigit():
        try:
            m = await context.bot.get_chat_member(update.effective_chat.id, int(ident))
            return m.user
        except Exception:
            return None
    row = await find_user_by_username(pool, ident.lstrip("@"))
    if row:
        return SimpleNamespace(id=row["id"], first_name=row["first_name"] or "User")
    return None


async def track_user(pool, user, chat):
    """Remember users and group members; never creates wallets."""
    if user is None or user.is_bot:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, first_name) VALUES ($1, $2, $3) "
            "ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username, "
            "first_name = EXCLUDED.first_name, updated_at = now()",
            user.id, user.username, user.first_name,
        )
        await conn.execute(
            "INSERT INTO ax_user_stats (user_id, message_count) VALUES ($1, 1) "
            "ON CONFLICT (user_id) DO UPDATE SET last_seen = now(), "
            "message_count = ax_user_stats.message_count + 1",
            user.id,
        )
        if chat is not None and chat.type != "private":
            await conn.execute(
                "INSERT INTO ax_chat_members (chat_id, user_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                chat.id, user.id,
            )

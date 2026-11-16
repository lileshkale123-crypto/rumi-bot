"""Shared helpers for the Astraea-style features in Rumi."""
import asyncio
import html
import logging
from types import SimpleNamespace

from telegram import InlineKeyboardButton
from telegram.constants import ChatMemberStatus

log = logging.getLogger(__name__)

ADMIN_STATES = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


def esc(text) -> str:
    return html.escape(str(text))


def btn(text, data=None, url=None, style=None):
    """style: 'success' green, 'danger' red, 'primary' blue, None normal."""
    kw = {"api_kwargs": {"style": style}} if style else {}
    if url:
        return InlineKeyboardButton(text, url=url, **kw)
    return InlineKeyboardButton(text, callback_data=data, **kw)


import time as _time

ANON_ON = set()
ANON_ID = 1087968824  # GroupAnonymousBot
_ADMIN_CACHE = {}     # (chat_id, user_id) -> (ChatMember, timestamp)
ADMIN_TTL = 120       # safe: chat_member updates invalidate instantly (see ax_addprompt)

RIGHT_NAMES = {
    "can_restrict_members": "Ban users",
    "can_delete_messages": "Delete messages",
    "can_pin_messages": "Pin messages",
    "can_change_info": "Change group info",
    "can_promote_members": "Add new admins",
    "can_invite_users": "Invite users",
}


def fire(coro):
    """Run a slow side job (log, DB write) in the background so the reply is not delayed."""
    async def _run():
        try:
            await coro
        except Exception:
            log.exception("background job failed")
    try:
        return asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        coro.close()


def invalidate(chat_id, user_id=None):
    if user_id is None:
        for k in [k for k in _ADMIN_CACHE if k[0] == chat_id]:
            _ADMIN_CACHE.pop(k, None)
    else:
        _ADMIN_CACHE.pop((chat_id, user_id), None)
    try:
        import ax_lockcore
        ax_lockcore._admin_cache.clear()
    except Exception:
        pass


def remember(chat_id, member):
    _ADMIN_CACHE[(chat_id, member.user.id)] = (member, _time.time())


async def get_member(bot, chat_id, user_id):
    """ChatMember (cached). None when Telegram can't tell us."""
    now = _time.time()
    hit = _ADMIN_CACHE.get((chat_id, user_id))
    if hit and now - hit[1] < ADMIN_TTL:
        return hit[0]
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return None
    _ADMIN_CACHE[(chat_id, user_id)] = (m, now)
    return m


async def is_admin(bot, chat_id, user_id, right=None) -> bool:
    """True if the user is an admin. If `right` is given (e.g. "can_restrict_members")
    the admin must also hold that permission. The owner holds every permission."""
    if user_id == ANON_ID:
        return chat_id in ANON_ON  # anonymous admins can't be checked per right
    m = await get_member(bot, chat_id, user_id)
    if m is None or m.status not in ADMIN_STATES:
        return False
    if right is None or m.status == ChatMemberStatus.OWNER:
        return True
    return bool(getattr(m, right, False))


async def has_right(bot, chat_id, user_id, right) -> bool:
    return await is_admin(bot, chat_id, user_id, right)


async def bot_is_admin(bot, chat_id, right=None) -> bool:
    return await is_admin(bot, chat_id, bot.id, right)


def right_label(right):
    return RIGHT_NAMES.get(right, "the needed permission")


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
            m = await get_member(context.bot, update.effective_chat.id, int(ident))
            return m.user if m else None
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

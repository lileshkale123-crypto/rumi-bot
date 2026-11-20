"""Shared helpers for the Astraea-style features in Rumi (aiogram 3.x)."""
import asyncio
import html
import logging
import time as _time
from types import SimpleNamespace
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardButton, Message

log = logging.getLogger(__name__)

ADMIN_STATES = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)

ANON_ON: set[int] = set()
ANON_ID = 1087968824  # GroupAnonymousBot
_ADMIN_CACHE: dict[tuple[int, int], tuple[Any, float]] = {}  # (chat_id, user_id) -> (ChatMember, timestamp)
_ADMIN_CACHE_MAX = 5000
ADMIN_TTL = 120  # safe: chat_member updates invalidate instantly (see ax_addprompt)

_BOT_USERNAMES: dict[int, str] = {}
_BG_TASKS: set[asyncio.Task] = set()

RIGHT_NAMES = {
    "can_restrict_members": "Ban users",
    "can_delete_messages": "Delete messages",
    "can_pin_messages": "Pin messages",
    "can_change_info": "Change group info",
    "can_promote_members": "Add new admins",
    "can_invite_users": "Invite users",
}


def esc(text: Any) -> str:
    return html.escape(str(text))


def btn(
    text: str,
    data: str | None = None,
    url: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    """Build an InlineKeyboardButton with optional Bot API button style ('success', 'danger', 'primary')."""
    kwargs: dict[str, Any] = {"text": text}
    if url:
        kwargs["url"] = url
    else:
        kwargs["callback_data"] = data
    if style:
        try:
            return InlineKeyboardButton(**kwargs, style=style)
        except (TypeError, ValueError):
            pass
    return InlineKeyboardButton(**kwargs)


def cmd_args(message: Message | None) -> list[str]:
    """Return whitespace-split arguments after a /command (works for text and caption)."""
    if message is None:
        return []
    raw = (message.text or message.caption or "").strip()
    parts = raw.split()
    return parts[1:] if len(parts) > 1 else []


def cmd_arg_text(message: Message | None) -> str:
    """Return raw text after the /command preserving line breaks."""
    if message is None:
        return ""
    raw = (message.text or message.caption or "").strip()
    parts = raw.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def get_bot_username(bot: Bot) -> str:
    """Return cached bot username without repeated getMe API calls."""
    cached = _BOT_USERNAMES.get(bot.id)
    if cached:
        return cached
    me = await bot.me()
    uname = me.username or ""
    if uname:
        _BOT_USERNAMES[bot.id] = uname
    return uname


def fire(coro):
    """Run a slow side job (log, DB write) in the background so replies are never delayed."""
    async def _run():
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background job failed")

    try:
        task = asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        coro.close()
        return None
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def _prune_admin_cache(now: float) -> None:
    if len(_ADMIN_CACHE) <= _ADMIN_CACHE_MAX:
        return
    expired = [k for k, (_, ts) in _ADMIN_CACHE.items() if now - ts >= ADMIN_TTL]
    for k in expired:
        _ADMIN_CACHE.pop(k, None)
    if len(_ADMIN_CACHE) > _ADMIN_CACHE_MAX:
        # Evict oldest 20%
        oldest = sorted(_ADMIN_CACHE.items(), key=lambda kv: kv[1][1])[: _ADMIN_CACHE_MAX // 5]
        for k, _ in oldest:
            _ADMIN_CACHE.pop(k, None)


def invalidate(chat_id: int, user_id: int | None = None) -> None:
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


def remember(chat_id: int, member) -> None:
    now = _time.time()
    _prune_admin_cache(now)
    _ADMIN_CACHE[(chat_id, member.user.id)] = (member, now)


async def get_member(bot: Bot, chat_id: int, user_id: int):
    """ChatMember (cached). Returns None when Telegram can't tell us."""
    now = _time.time()
    hit = _ADMIN_CACHE.get((chat_id, user_id))
    if hit and now - hit[1] < ADMIN_TTL:
        return hit[0]
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return None
    _prune_admin_cache(now)
    _ADMIN_CACHE[(chat_id, user_id)] = (member, now)
    return member


async def is_admin(bot: Bot, chat_id: int, user_id: int, right: str | None = None) -> bool:
    """True if the user is an admin. If `right` is given (e.g. 'can_restrict_members'),
    the admin must also hold that permission. The group creator holds every permission."""
    if user_id == ANON_ID:
        return chat_id in ANON_ON  # anonymous admins can't be checked per right
    member = await get_member(bot, chat_id, user_id)
    if member is None or member.status not in ADMIN_STATES:
        return False
    if right is None or member.status == ChatMemberStatus.CREATOR:
        return True
    return bool(getattr(member, right, False))


async def has_right(bot: Bot, chat_id: int, user_id: int, right: str) -> bool:
    return await is_admin(bot, chat_id, user_id, right)


async def bot_is_admin(bot: Bot, chat_id: int, right: str | None = None) -> bool:
    return await is_admin(bot, chat_id, bot.id, right)


def right_label(right: str | None) -> str:
    return RIGHT_NAMES.get(right or "", "the needed permission")


async def find_user_by_username(pool, username: str):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, first_name FROM users WHERE username ILIKE $1",
            username,
        )


async def resolve_target(
    message: Message,
    bot: Bot,
    pool,
    args: list[str] | None = None,
):
    """Resolve a target user via reply, text_mention, numeric id, or @username.
    Returns a User or SimpleNamespace(id, first_name, username, is_bot), or None."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    for ent in message.entities or []:
        if ent.type == "text_mention" and ent.user:
            return ent.user

    parsed_args = cmd_args(message) if args is None else args
    if not parsed_args:
        return None

    ident = parsed_args[0]
    if ident.lstrip("-").isdigit():
        try:
            member = await get_member(bot, message.chat.id, int(ident))
            return member.user if member else None
        except Exception:
            return None

    uname = ident.lstrip("@")
    row = await find_user_by_username(pool, uname)
    if row:
        return SimpleNamespace(
            id=row["id"],
            first_name=row["first_name"] or "User",
            username=uname,
            is_bot=False,
        )
    return None


async def track_user(pool, user, chat) -> None:
    """Remember users and group members; never creates wallets."""
    if user is None or user.is_bot:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, first_name) VALUES ($1, $2, $3) "
            "ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username, "
            "first_name = EXCLUDED.first_name, updated_at = now()",
            user.id,
            user.username,
            user.first_name,
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
                chat.id,
                user.id,
            )

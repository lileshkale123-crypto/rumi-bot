"""When Rumi is added to a group: ask for admin rights, warn when rights are missing,
and keep the admin-permission cache correct instantly (aiogram 3.x)."""
import html
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, TelegramObject

import ax_common
from ax_common import btn, fire, get_bot_username

log = logging.getLogger(__name__)
H = ParseMode.HTML
GROUPS = ("group", "supergroup")
ADMIN = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
ASK_RIGHTS = (
    "change_info+delete_messages+restrict_members+invite_users+"
    "pin_messages+manage_video_chats+promote_members"
)
NEEDED = (
    ("can_delete_messages", "Delete messages"),
    ("can_restrict_members", "Ban users"),
    ("can_pin_messages", "Pin messages"),
    ("can_promote_members", "Add new admins"),
)

_last: dict[int, float] = {}
_LAST_MAX = 5000
_middleware_registered = False


def _link(username: str) -> str:
    return f"https://t.me/{username}?startgroup=true&admin={ASK_RIGHTS}"


def _prune_last(now: float) -> None:
    if len(_last) <= _LAST_MAX:
        return
    stale = [cid for cid, ts in _last.items() if now - ts >= 60.0]
    for cid in stale:
        _last.pop(cid, None)


async def _tell(bot: Bot, chat_id: int, adder, text: str, username: str) -> None:
    now = time.time()
    if now - _last.get(chat_id, 0.0) < 20.0:
        return
    _prune_last(now)
    _last[chat_id] = now

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("✅ Make me admin", url=_link(username), style="success")]]
    )
    try:
        await bot.send_message(chat_id, text, parse_mode=H, reply_markup=kb)
        return
    except Exception:
        pass
    if adder is not None and not adder.is_bot:  # group blocks us from talking: tell the adder in DM
        try:
            await bot.send_message(adder.id, text, parse_mode=H, reply_markup=kb)
        except Exception:
            pass


async def on_my_member(r: ChatMemberUpdated, bot: Bot) -> None:
    if r.chat.type not in GROUPS:
        return
    chat_id = r.chat.id
    ax_common.invalidate(chat_id, bot.id)
    old, new = r.old_chat_member, r.new_chat_member
    username = await get_bot_username(bot)
    adder = r.from_user
    title = html.escape(r.chat.title or "this group")

    if new.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED):
        if old.status in ADMIN:
            text = (
                "⚠️ <b>I lost my admin rights</b>\n<blockquote>Without admin I can't ban, mute, "
                "delete messages or read every message. Please make me admin again ✨</blockquote>"
            )
        else:
            text = (
                f"👋 <b>Thanks for adding me to {title}!</b>\n"
                "<blockquote>I'm <b>not an admin</b> yet, so I can't moderate and I can't see "
                "most messages.\nTap the button below and make me an admin 💫</blockquote>"
            )
        await _tell(bot, chat_id, adder, text, username)
        return

    if new.status == ChatMemberStatus.ADMINISTRATOR:
        missing = [label for attr, label in NEEDED if not getattr(new, attr, False)]
        was = {a: getattr(old, a, False) for a, _ in NEEDED} if old.status in ADMIN else None
        now_rights = {a: getattr(new, a, False) for a, _ in NEEDED}
        if missing and was != now_rights:
            text = (
                "⚠️ <b>I'm admin, but some permissions are missing</b>\n<blockquote>"
                + "\n".join(f"❌ {m}" for m in missing)
                + "\n\nGive me these so every command works 💫</blockquote>"
            )
            await _tell(bot, chat_id, adder, text, username)


class MyMemberPromptMiddleware(BaseMiddleware):
    """Non-blocking observer on my_chat_member for admin prompts & cache invalidation."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, ChatMemberUpdated):
            bot: Bot | None = data.get("bot")
            if bot is not None:
                fire(on_my_member(event, bot))
        return await handler(event, data)


class ChatMemberInvalidateMiddleware(BaseMiddleware):
    """Invalidate admin cache instantly whenever any chat member's status/rights change."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, ChatMemberUpdated):
            _adm = ax_common.ADMIN_STATES
            ax_common.invalidate(
                event.chat.id,
                event.new_chat_member.user.id,
                admin_change=(event.old_chat_member.status in _adm or event.new_chat_member.status in _adm),
            )
            if event.old_chat_member.status in _adm or event.new_chat_member.status in _adm:
                _b = data.get("bot")
                if _b is not None:
                    ax_common.warm_admins(_b, event.chat.id)
        return await handler(event, data)


def register(dp) -> None:
    global _middleware_registered
    if _middleware_registered:
        return
    _middleware_registered = True
    dp.chat_member.outer_middleware(ChatMemberInvalidateMiddleware())
    dp.my_chat_member.outer_middleware(MyMemberPromptMiddleware())

"""When Rumi is added to a group: ask for admin rights (like other bots do), warn when
rights are missing, and keep the admin-permission cache correct instantly."""
import html
import logging
import time

from telegram import InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import ChatMemberHandler

import ax_common
from ax_common import btn

log = logging.getLogger(__name__)
H = ParseMode.HTML
GROUPS = ("group", "supergroup")
ADMIN = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
ASK_RIGHTS = "change_info+delete_messages+restrict_members+invite_users+pin_messages+manage_video_chats+promote_members"
NEEDED = (
    ("can_delete_messages", "Delete messages"),
    ("can_restrict_members", "Ban users"),
    ("can_pin_messages", "Pin messages"),
    ("can_promote_members", "Add new admins"),
)
_last = {}


def _link(username):
    return f"https://t.me/{username}?startgroup=true&admin={ASK_RIGHTS}"


async def _tell(context, chat_id, adder, text, username):
    now = time.time()
    if now - _last.get(chat_id, 0) < 20:
        return
    _last[chat_id] = now
    kb = InlineKeyboardMarkup([[btn("✅ Make me admin", url=_link(username), style="success")]])
    try:
        await context.bot.send_message(chat_id, text, parse_mode=H, reply_markup=kb)
        return
    except Exception:
        pass
    if adder is not None and not adder.is_bot:  # group blocks us from talking: tell the adder in DM
        try:
            await context.bot.send_message(adder.id, text, parse_mode=H, reply_markup=kb)
        except Exception:
            pass


async def on_my_member(update, context):
    r = update.my_chat_member
    if r is None or r.chat.type not in GROUPS:
        return
    chat_id = r.chat.id
    ax_common.invalidate(chat_id, context.bot.id)
    old, new = r.old_chat_member, r.new_chat_member
    username = context.bot.username
    adder = r.from_user
    title = html.escape(r.chat.title or "this group")

    if new.status == ChatMemberStatus.MEMBER or new.status == ChatMemberStatus.RESTRICTED:
        if old.status in ADMIN:
            text = ("⚠️ <b>I lost my admin rights</b>\n<blockquote>Without admin I can't ban, mute, "
                    "delete messages or read every message. Please make me admin again ✨</blockquote>")
        else:
            text = (f"👋 <b>Thanks for adding me to {title}!</b>\n"
                    "<blockquote>I'm <b>not an admin</b> yet, so I can't moderate and I can't see "
                    "most messages.\nTap the button below and make me an admin 💫</blockquote>")
        await _tell(context, chat_id, adder, text, username)
        return

    if new.status == ChatMemberStatus.ADMINISTRATOR:
        missing = [label for attr, label in NEEDED if not getattr(new, attr, False)]
        was = {a: getattr(old, a, False) for a, _ in NEEDED} if old.status in ADMIN else None
        now_rights = {a: getattr(new, a, False) for a, _ in NEEDED}
        if missing and was != now_rights:
            text = ("⚠️ <b>I'm admin, but some permissions are missing</b>\n<blockquote>"
                    + "\n".join(f"❌ {m}" for m in missing)
                    + "\n\nGive me these so every command works 💫</blockquote>")
            await _tell(context, chat_id, adder, text, username)


async def on_member(update, context):
    """Any admin change in the group: forget what we remembered, so rights are never stale."""
    c = update.chat_member
    if c is None:
        return
    ax_common.invalidate(c.chat.id, c.new_chat_member.user.id)


def register(app):
    app.add_handler(ChatMemberHandler(on_member, ChatMemberHandler.CHAT_MEMBER), group=-22)
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER), group=-21)

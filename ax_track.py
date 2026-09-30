"""Remembers users and groups so @username targeting works."""
from telegram.constants import ChatMemberStatus
from telegram.ext import ChatMemberHandler, MessageHandler, filters

from ax_common import track_user

_seen_groups = set()


async def tracker(update, context):
    chat, user = update.effective_chat, update.effective_user
    pool = context.bot_data["pool"]
    try:
        await track_user(pool, user, chat)
        if chat and chat.type != "private" and chat.id not in _seen_groups:
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ax_groups (chat_id, title) VALUES ($1, $2) "
                    "ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title",
                    chat.id, chat.title or "Group")
            _seen_groups.add(chat.id)
    except Exception as e:
        print("tracker error:", e)


async def my_chat_member(update, context):
    r = update.my_chat_member
    if not r or r.chat.type == "private":
        return
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        if r.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            await c.execute("DELETE FROM ax_groups WHERE chat_id=$1", r.chat.id)
            _seen_groups.discard(r.chat.id)
        else:
            await c.execute(
                "INSERT INTO ax_groups (chat_id, title) VALUES ($1, $2) "
                "ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title",
                r.chat.id, r.chat.title or "Group")
            _seen_groups.add(r.chat.id)


def register(app):
    app.add_handler(MessageHandler(filters.ALL, tracker), group=-5)
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

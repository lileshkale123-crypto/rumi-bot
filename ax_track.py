"""Remembers users and groups so @username targeting works."""
from telegram.constants import ChatMemberStatus
from telegram.ext import ChatMemberHandler, MessageHandler, filters

from ax_common import track_user

_seen_groups = set()
_known = {}
_counts = {}


async def _track_cached(pool, user, chat):
    if user is None or user.is_bot:
        return
    gid = 0
    if chat is not None and chat.type == "private":
        gid = 0
    elif chat is not None:
        gid = chat.id
    key = (user.id, gid)
    sig = (user.username, user.first_name)
    if _known.get(key) == sig:
        _counts[user.id] = _counts.get(user.id, 0) + 1
        return
    await track_user(pool, user, chat)
    _known[key] = sig


async def _flush_counts(context):
    if not _counts:
        return
    items = list(_counts.items())
    _counts.clear()
    pool = context.bot_data["pool"]
    try:
        async with pool.acquire() as c:
            await c.executemany(
                "UPDATE ax_user_stats SET message_count = message_count + $2, "
                "last_seen = now() WHERE user_id = $1",
                items,
            )
    except Exception as e:
        print("flush counts error:", e)
        for uid, n in items:
            _counts[uid] = _counts.get(uid, 0) + n


async def tracker(update, context):
    chat, user = update.effective_chat, update.effective_user
    pool = context.bot_data["pool"]
    try:
        await _track_cached(pool, user, chat)
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
    app.job_queue.run_repeating(_flush_counts, interval=300, first=300)
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))


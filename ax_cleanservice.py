"""Delete Telegram service messages (joins, leaves, pins, etc)."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from ax_common import is_admin

H = ParseMode.HTML
TYPES = ("join", "leave", "other", "photo", "pin", "title", "videochat")
_cache = {}


async def kinds(pool, chat_id):
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT kind FROM ax_cleanservice WHERE chat_id=$1", chat_id)
    k = {r["kind"] for r in rows}
    _cache[chat_id] = k
    return k


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private" or not await is_admin(
            context.bot, chat.id, user.id):
        await update.effective_message.reply_text(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group "
            "as an admin ✨</blockquote>", parse_mode=H)
        return None
    return context.bot_data["pool"], chat.id


def _parse(args):
    out = set()
    for a in args:
        a = a.lower()
        if a == "all":
            out |= set(TYPES)
        elif a in TYPES:
            out.add(a)
    return out


async def cleanservice(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    want = _parse(context.args)
    if not want:
        cur = await kinds(pool, cid)
        await update.effective_message.reply_text(
            "🧹 <b>Clean service</b>\n<blockquote>On for: <b>"
            f"{', '.join(sorted(cur)) or 'nothing'}</b>\n"
            "Use /cleanservice all, join, leave, other, photo, pin, "
            "title or videochat</blockquote>", parse_mode=H)
        return
    async with pool.acquire() as c:
        for k in want:
            await c.execute(
                "INSERT INTO ax_cleanservice VALUES ($1,$2) "
                "ON CONFLICT DO NOTHING", cid, k)
    await kinds(pool, cid)
    await update.effective_message.reply_text(
        f"🧹 <b>Cleaning</b> <b>{', '.join(sorted(want))}</b> ✨",
        parse_mode=H)


async def keepservice(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    want = _parse(context.args)
    if not want:
        await update.effective_message.reply_text(
            "<blockquote>Use /keepservice all, join, leave, other, "
            "photo, pin, title or videochat</blockquote>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_cleanservice WHERE chat_id=$1 AND "
            "kind=ANY($2)", cid, list(want))
    await kinds(pool, cid)
    await update.effective_message.reply_text(
        f"✅ <b>Keeping</b> <b>{', '.join(sorted(want))}</b>",
        parse_mode=H)


async def types(update, context):
    await update.effective_message.reply_text(
        "🧹 <b>Service types</b>\n<blockquote>join: new members\n"
        "leave: members leaving\nphoto: group photo changes\n"
        "pin: pinned message notices\ntitle: name changes\n"
        "videochat: video chat notices\nother: everything else\n"
        "all: every type</blockquote>", parse_mode=H)


def kind_of(m):
    if m.new_chat_members:
        return "join"
    if m.left_chat_member:
        return "leave"
    if m.new_chat_photo or m.delete_chat_photo:
        return "photo"
    if m.pinned_message:
        return "pin"
    if m.new_chat_title:
        return "title"
    if (m.video_chat_started or m.video_chat_ended
            or m.video_chat_scheduled
            or m.video_chat_participants_invited):
        return "videochat"
    if (m.group_chat_created or m.supergroup_chat_created
            or m.channel_chat_created or m.message_auto_delete_timer_changed
            or m.migrate_to_chat_id or m.migrate_from_chat_id):
        return "other"
    return None


async def _del(context):
    try:
        await context.bot.delete_message(*context.job.data)
    except Exception:
        pass


async def watcher(update, context):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or chat.type == "private":
        return
    kind = kind_of(msg)
    if not kind:
        return
    k = _cache.get(chat.id)
    if k is None:
        k = await kinds(context.bot_data["pool"], chat.id)
    if kind in k:
        context.job_queue.run_once(_del, 2, data=(chat.id, msg.message_id))


def register(app):
    app.add_handler(MessageHandler(
        filters.StatusUpdate.ALL & filters.ChatType.GROUPS, watcher),
        group=-8)
    for n, f in (("cleanservice", cleanservice),
                 ("keepservice", keepservice),
                 ("nocleanservice", keepservice),
                 ("cleanservicetypes", types)):
        app.add_handler(CommandHandler(n, f))

"""Clean command messages after they run."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from ax_common import is_admin

H = ParseMode.HTML
TYPES = ("admin", "user", "other")
_cache = {}


async def kinds(pool, chat_id):
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT kind FROM ax_cleancmd WHERE chat_id=$1", chat_id)
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


async def cleancommand(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    want = _parse(context.args)
    if not want:
        cur = await kinds(pool, cid)
        await update.effective_message.reply_text(
            "🧹 <b>Clean commands</b>\n<blockquote>On for: <b>"
            f"{', '.join(sorted(cur)) or 'nothing'}</b>\n"
            "Use /cleancommand all, admin, user or other</blockquote>",
            parse_mode=H)
        return
    async with pool.acquire() as c:
        for k in want:
            await c.execute(
                "INSERT INTO ax_cleancmd VALUES ($1,$2) "
                "ON CONFLICT DO NOTHING", cid, k)
    await kinds(pool, cid)
    await update.effective_message.reply_text(
        f"🧹 <b>Cleaning</b> <b>{', '.join(sorted(want))}</b> commands ✨",
        parse_mode=H)


async def keepcommand(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    want = _parse(context.args)
    if not want:
        await update.effective_message.reply_text(
            "<blockquote>Use /keepcommand all, admin, user or other"
            "</blockquote>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_cleancmd WHERE chat_id=$1 AND kind=ANY($2)",
            cid, list(want))
    await kinds(pool, cid)
    await update.effective_message.reply_text(
        f"✅ <b>Keeping</b> <b>{', '.join(sorted(want))}</b> commands",
        parse_mode=H)


async def types(update, context):
    await update.effective_message.reply_text(
        "🧹 <b>Command types</b>\n<blockquote>admin: sent by admins\n"
        "user: sent by members\nother: aimed at other bots\n"
        "all: every type</blockquote>", parse_mode=H)


async def _del(context):
    try:
        await context.bot.delete_message(*context.job.data)
    except Exception:
        pass


async def watcher(update, context):
    msg, chat, user = (update.effective_message, update.effective_chat,
                       update.effective_user)
    if not msg or not user or chat.type == "private" or not msg.text:
        return
    first = msg.text.split()[0]
    pool = context.bot_data["pool"]
    k = _cache.get(chat.id)
    if k is None:
        k = await kinds(pool, chat.id)
    if not k:
        return
    me = "@" + (context.bot.username or "").lower()
    if "@" in first and not first.lower().endswith(me):
        kind = "other"
    elif await is_admin(context.bot, chat.id, user.id):
        kind = "admin"
    else:
        kind = "user"
    if kind in k:
        context.job_queue.run_once(
            _del, 3, data=(chat.id, msg.message_id))


def register(app):
    app.add_handler(MessageHandler(
        filters.COMMAND & filters.ChatType.GROUPS, watcher), group=-8)
    app.add_handler(CommandHandler("cleancommand", cleancommand))
    app.add_handler(CommandHandler("keepcommand", keepcommand))
    app.add_handler(CommandHandler("cleancommandtypes", types))

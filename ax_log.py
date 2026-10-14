"""Log channel support: send mod actions to a linked channel."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import esc, is_admin

H = ParseMode.HTML
CATS = ("ban", "mute", "kick", "warn", "lock", "blocklist", "flood",
        "settings")


async def _row(pool, chat_id):
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT log_chat, off_cats FROM ax_log_settings "
            "WHERE chat_id=$1", chat_id)
    if not r:
        return None, set()
    return r["log_chat"], {x for x in (r["off_cats"] or "").split(",") if x}


async def send_log(bot, pool, chat_id, cat, text):
    """Send one log line. Never raises."""
    try:
        log_chat, off = await _row(pool, chat_id)
        if not log_chat or cat in off:
            return
        title = ""
        try:
            title = (await bot.get_chat(chat_id)).title or ""
        except Exception:
            pass
        await bot.send_message(
            log_chat,
            f"📋 <b>{esc(title)}</b> · <i>{cat}</i>\n"
            f"<blockquote>{text}</blockquote>", parse_mode=H)
    except Exception:
        pass


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private" or not await is_admin(
            context.bot, chat.id, user.id):
        await update.effective_message.reply_text(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group "
            "as an admin ✨</blockquote>", parse_mode=H)
        return None
    return context.bot_data["pool"], chat.id


async def setlog(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    msg = update.effective_message
    target = None
    fwd = getattr(msg, "forward_origin", None)
    if fwd is not None and getattr(fwd, "chat", None):
        target = fwd.chat.id
    elif context.args:
        try:
            target = int(context.args[0])
        except ValueError:
            target = None
    if not target:
        await msg.reply_text(
            "📋 <b>Set log channel</b>\n<blockquote>Use /setlog "
            "-100123... (channel id)\nThe bot must be admin there ✨"
            "</blockquote>", parse_mode=H)
        return
    try:
        await context.bot.send_message(target, "✅ Log channel linked ✨")
    except Exception:
        await msg.reply_text(
            "⚠️ <b>Cannot post there</b>\n<blockquote>Add me as an admin "
            "in that channel first ✨</blockquote>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_log_settings (chat_id, log_chat) VALUES "
            "($1,$2) ON CONFLICT (chat_id) DO UPDATE SET log_chat=$2",
            cid, target)
    await msg.reply_text("✅ <b>Log channel set</b>", parse_mode=H)


async def unsetlog(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE ax_log_settings SET log_chat=NULL WHERE chat_id=$1",
            cid)
    await update.effective_message.reply_text(
        "🚫 <b>Log channel removed</b>", parse_mode=H)


async def logchannel(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    lc, off = await _row(pool, cid)
    await update.effective_message.reply_text(
        f"📋 <b>Log channel</b>\n<blockquote>Linked: "
        f"<b>{lc or 'none'}</b>\nOff: <b>{', '.join(sorted(off)) or 'none'}"
        f"</b></blockquote>", parse_mode=H)


def _toggle(turn_on):
    async def cmd(update, context):
        g = await _gate(update, context)
        if not g:
            return
        pool, cid = g
        a = (context.args[0].lower() if context.args else "")
        if a not in CATS:
            await update.effective_message.reply_text(
                "<blockquote>Pick one: " + ", ".join(CATS) + "</blockquote>",
                parse_mode=H)
            return
        _, off = await _row(pool, cid)
        off.discard(a) if turn_on else off.add(a)
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_log_settings (chat_id, off_cats) VALUES "
                "($1,$2) ON CONFLICT (chat_id) DO UPDATE SET off_cats=$2",
                cid, ",".join(sorted(off)))
        await update.effective_message.reply_text(
            f"{'✅' if turn_on else '🚫'} <b>{a} logs "
            f"{'on' if turn_on else 'off'}</b>", parse_mode=H)
    return cmd


async def logcategories(update, context):
    await update.effective_message.reply_text(
        "📋 <b>Log categories</b>\n<blockquote>" + "\n".join(CATS)
        + "</blockquote>", parse_mode=H)


def register(app):
    for n, f in (("setlog", setlog), ("unsetlog", unsetlog),
                 ("logchannel", logchannel), ("log", _toggle(True)),
                 ("nolog", _toggle(False)),
                 ("logcategories", logcategories)):
        app.add_handler(CommandHandler(n, f))

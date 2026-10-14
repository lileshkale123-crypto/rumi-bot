"""Disable and enable commands per chat."""
from telegram.constants import ParseMode
from telegram.ext import (ApplicationHandlerStop, CommandHandler,
                          MessageHandler, filters)

from ax_common import esc, is_admin

H = ParseMode.HTML
PROTECTED = {"disable", "enable", "disabled", "disableable", "disabledel",
             "disableadmin", "start", "help"}
_cache = {}


async def load(pool, chat_id):
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT cmd FROM ax_disabled WHERE chat_id=$1", chat_id)
        s = await c.fetchrow(
            "SELECT del_msg, admin_too FROM ax_disable_settings "
            "WHERE chat_id=$1", chat_id)
    d = ({r["cmd"] for r in rows}, bool(s and s["del_msg"]),
         bool(s and s["admin_too"]))
    _cache[chat_id] = d
    return d


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private" or not await is_admin(
            context.bot, chat.id, user.id):
        await update.effective_message.reply_text(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group "
            "as an admin ✨</blockquote>", parse_mode=H)
        return None
    return context.bot_data["pool"], chat.id


def _name(args):
    return args[0].lower().lstrip("/").split("@")[0] if args else ""


async def disable(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    n = _name(context.args)
    msg = update.effective_message
    if not n:
        await msg.reply_text("<blockquote>Use /disable command</blockquote>",
                             parse_mode=H)
        return
    if n in PROTECTED:
        await msg.reply_text(
            "🔒 <b>Protected</b>\n<blockquote>That command cannot be "
            "disabled ✨</blockquote>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await c.execute("INSERT INTO ax_disabled VALUES ($1,$2) "
                        "ON CONFLICT DO NOTHING", cid, n)
    await load(pool, cid)
    await msg.reply_text(f"🚫 <b>/{esc(n)} disabled</b>", parse_mode=H)


async def enable(update, context):
    g = await _gate(update, context)
    if not g:
        return
    pool, cid = g
    n = _name(context.args)
    if not n:
        await update.effective_message.reply_text(
            "<blockquote>Use /enable command</blockquote>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_disabled WHERE chat_id=$1 AND "
                        "cmd=$2", cid, n)
    await load(pool, cid)
    await update.effective_message.reply_text(
        f"✅ <b>/{esc(n)} enabled</b>", parse_mode=H)


async def disabled(update, context):
    chat = update.effective_chat
    d, _, _ = await load(context.bot_data["pool"], chat.id)
    body = "\n".join("/" + esc(x) for x in sorted(d)) or "nothing"
    await update.effective_message.reply_text(
        f"🚫 <b>Disabled commands</b>\n<blockquote>{body}</blockquote>",
        parse_mode=H)


async def disableable(update, context):
    await update.effective_message.reply_text(
        "🔒 <b>Cannot be disabled</b>\n<blockquote>"
        + ", ".join("/" + p for p in sorted(PROTECTED))
        + "\nEvery other command can ✨</blockquote>", parse_mode=H)


def _flag(col, name):
    async def cmd(update, context):
        g = await _gate(update, context)
        if not g:
            return
        pool, cid = g
        a = (context.args[0].lower() if context.args else "")
        if a not in ("on", "off", "yes", "no"):
            await update.effective_message.reply_text(
                f"<blockquote>Use /{name} on or off</blockquote>",
                parse_mode=H)
            return
        v = a in ("on", "yes")
        async with pool.acquire() as c:
            await c.execute(
                f"INSERT INTO ax_disable_settings (chat_id, {col}) "
                f"VALUES ($1,$2) ON CONFLICT (chat_id) DO UPDATE SET "
                f"{col}=$2", cid, v)
        await load(pool, cid)
        await update.effective_message.reply_text(
            f"{'✅' if v else '🚫'} <b>{name} {'on' if v else 'off'}</b>",
            parse_mode=H)
    return cmd


async def watcher(update, context):
    msg, chat, user = (update.effective_message, update.effective_chat,
                       update.effective_user)
    if not msg or not user or chat.type == "private" or not msg.text:
        return
    first = msg.text.split()[0][1:].lower().split("@")[0]
    if first in PROTECTED:
        return
    d = _cache.get(chat.id) or await load(context.bot_data["pool"], chat.id)
    cmds, del_msg, admin_too = d
    if first not in cmds:
        return
    if not admin_too and await is_admin(context.bot, chat.id, user.id):
        return
    if del_msg:
        try:
            await msg.delete()
        except Exception:
            pass
    raise ApplicationHandlerStop


def register(app):
    app.add_handler(MessageHandler(
        filters.COMMAND & filters.ChatType.GROUPS, watcher), group=-9)
    for n, f in (("disable", disable), ("enable", enable),
                 ("disabled", disabled), ("disableable", disableable),
                 ("disabledel", _flag("del_msg", "disabledel")),
                 ("disableadmin", _flag("admin_too", "disableadmin"))):
        app.add_handler(CommandHandler(n, f))

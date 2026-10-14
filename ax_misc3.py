"""anonadmin, adminerror, silentactions + admin cache clearing."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

import ax_common
from ax_common import is_admin

H = ParseMode.HTML
SILENT = set()
QUIET = set()


async def load(app):
    pool = app.bot_data["pool"]
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT chat_id, anon_admin, admin_error, silent_actions "
            "FROM ax_misc_settings")
    for r in rows:
        if r["anon_admin"]:
            ax_common.ANON_ON.add(r["chat_id"])
        if r["silent_actions"]:
            SILENT.add(r["chat_id"])
        if not r["admin_error"]:
            QUIET.add(r["chat_id"])


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private" or not await is_admin(
            context.bot, chat.id, user.id):
        await update.effective_message.reply_text(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group "
            "as an admin ✨</blockquote>", parse_mode=H)
        return None
    return context.bot_data["pool"], chat.id


def _yn(args):
    a = (args[0].lower() if args else "")
    if a in ("on", "yes"):
        return True
    if a in ("off", "no"):
        return False
    return None


async def _save(pool, cid, col, v):
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_misc_settings (chat_id, {col}) VALUES "
            f"($1,$2) ON CONFLICT (chat_id) DO UPDATE SET {col}=$2",
            cid, v)


def make(col, name, store, invert=False):
    async def cmd(update, context):
        g = await _gate(update, context)
        if not g:
            return
        pool, cid = g
        v = _yn(context.args)
        cur = ((cid not in store) if invert else (cid in store))
        msg = update.effective_message
        if v is None:
            await msg.reply_text(
                f"{'✅' if cur else '🚫'} <b>{name} is {'on' if cur else 'off'}</b>\n"
                f"<blockquote>Use /{name} on or off to change it</blockquote>",
                parse_mode=H)
            return
        if name == "silentactions" and v:
            async with pool.acquire() as c:
                r = await c.fetchrow(
                    "SELECT log_chat FROM ax_log_settings WHERE chat_id=$1",
                    cid)
            if not r or not r["log_chat"]:
                await msg.reply_text(
                    "⚠️ <b>Needs a log channel</b>\n<blockquote>Link one "
                    "with /setlog first ✨</blockquote>", parse_mode=H)
                return
        await _save(pool, cid, col, v)
        on = (not v) if invert else v
        (store.add if on else store.discard)(cid)
        await msg.reply_text(
            f"{'✅' if v else '🚫'} <b>{name} {'on' if v else 'off'}</b>",
            parse_mode=H)
    return cmd


async def clear_cache(update, context):
    ax_common._ADMIN_CACHE.clear()


def register(app):
    app.add_handler(CommandHandler(
        "anonadmin", make("anon_admin", "anonadmin", ax_common.ANON_ON)))
    app.add_handler(CommandHandler(
        "adminerror", make("admin_error", "adminerror", QUIET, True)))
    app.add_handler(CommandHandler(
        "silentactions", make("silent_actions", "silentactions", SILENT)))
    app.add_handler(MessageHandler(
        filters.Regex(r"^/(promote|demote)") & filters.ChatType.GROUPS,
        clear_cache), group=5)
    app.job_queue.run_once(lambda ctx: load(ctx.application), 5)

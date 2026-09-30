"""Notes and filters."""
import re

from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CommandHandler, MessageHandler, filters

from ax_common import esc, is_admin

H = ParseMode.HTML
KINDS = ("photo", "video", "animation", "sticker", "voice", "audio", "document")


async def say(update, text):
    await update.effective_message.reply_text(text, parse_mode=H)


async def admin_only(update, context):
    chat = update.effective_chat
    if chat.type == "private" or not await is_admin(context.bot, chat.id, update.effective_user.id):
        await say(update, "🛡️ <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>")
        return False
    return True


def extract(reply, rest):
    if reply:
        for k in KINDS:
            obj = getattr(reply, k)
            if obj:
                fid = obj[-1].file_id if k == "photo" else obj.file_id
                text = None if k == "sticker" else (reply.caption or rest or None)
                return k, text, fid
        if reply.text and not rest:
            return "text", reply.text, None
    return "text", rest or None, None


async def deliver(msg, ctype, text, fid):
    async def go(pm):
        if ctype == "text":
            await msg.reply_text(text or "…", parse_mode=pm)
        elif ctype == "sticker":
            await msg.reply_sticker(fid)
        else:
            await getattr(msg, "reply_" + ctype)(fid, caption=text, parse_mode=pm)
    try:
        await go(H)
    except BadRequest:
        await go(None)


def make_save(table, col, label, usage):
    async def cmd(update, context):
        if not await admin_only(update, context):
            return
        msg = update.effective_message
        parts = (msg.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await say(update, f"📝 <b>Save a {label}</b>\n<blockquote>{usage}</blockquote>")
            return
        key = parts[1].lower()[:50]
        rest = parts[2].strip() if len(parts) > 2 else ""
        ctype, text, fid = extract(msg.reply_to_message, rest)
        if ctype == "text" and not text:
            await say(update, f"💫 <b>Add some content</b>\n<blockquote>{usage}</blockquote>")
            return
        async with context.bot_data["pool"].acquire() as c:
            await c.execute(
                f"INSERT INTO {table} (chat_id, {col}, content_type, text_content, file_id, created_by) "
                f"VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (chat_id, {col}) DO UPDATE SET "
                f"content_type=$3, text_content=$4, file_id=$5, created_by=$6",
                update.effective_chat.id, key, ctype, text, fid, update.effective_user.id)
        await say(update, f"✅ <b>{label.title()} saved</b>\n<blockquote>🏷️ <b>{esc(key)}</b></blockquote>")
    return cmd


def make_list(table, col, label, prefix, addcmd):
    async def cmd(update, context):
        async with context.bot_data["pool"].acquire() as c:
            rows = await c.fetch(f"SELECT {col} AS k FROM {table} WHERE chat_id=$1 ORDER BY {col}",
                                 update.effective_chat.id)
        if not rows:
            await say(update, f"📭 <b>No {label}s yet</b>\n<blockquote>Admins can add one with /{addcmd} ✨</blockquote>")
            return
        lines = "\n".join(f"• {prefix}{esc(r['k'])}" for r in rows)
        await say(update, f"📒 <b>Saved {label}s</b>\n<blockquote>{lines}</blockquote>")
    return cmd


def make_delete(table, col, label, cmdname):
    async def cmd(update, context):
        if not await admin_only(update, context):
            return
        if not context.args:
            await say(update, f"💫 <b>Which {label}?</b>\n<blockquote>Usage: /{cmdname} name</blockquote>")
            return
        async with context.bot_data["pool"].acquire() as c:
            r = await c.execute(f"DELETE FROM {table} WHERE chat_id=$1 AND {col}=$2",
                                update.effective_chat.id, context.args[0].lower())
        ok = r.endswith("1")
        await say(update, "🗑️ <b>Removed</b>" if ok else f"🔎 <b>Not found</b>\n<blockquote>No such {label} here 💫</blockquote>")
    return cmd


def make_clear(table, label):
    async def cmd(update, context):
        if not await admin_only(update, context):
            return
        async with context.bot_data["pool"].acquire() as c:
            await c.execute(f"DELETE FROM {table} WHERE chat_id=$1", update.effective_chat.id)
        await say(update, f"🗑️ <b>All {label}s cleared</b>")
    return cmd


# ===== get, filter watcher, register =====
async def get_cmd(update, context):
    if update.effective_chat.type == "private":
        await say(update, "⚠️ <b>Groups only</b>\n<blockquote>Notes live inside groups 💫</blockquote>")
        return
    if not context.args:
        await say(update, "💫 <b>Which note?</b>\n<blockquote>Usage: /get name\nSee all with /notes</blockquote>")
        return
    async with context.bot_data["pool"].acquire() as c:
        r = await c.fetchrow(
            "SELECT content_type, text_content, file_id FROM ax_notes WHERE chat_id=$1 AND name=$2",
            update.effective_chat.id, context.args[0].lower())
    if not r:
        await say(update, "🔎 <b>Not found</b>\n<blockquote>No such note here. Try /notes 💫</blockquote>")
        return
    try:
        await deliver(update.effective_message, r["content_type"], r["text_content"], r["file_id"])
    except Exception as e:
        await say(update, f"⚠️ <b>Couldn't send it</b>\n<blockquote>{esc(e)}</blockquote>")


async def filter_watcher(update, context):
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not msg or not msg.text or user is None or user.is_bot:
        return
    async with context.bot_data["pool"].acquire() as c:
        rows = await c.fetch(
            "SELECT trigger_text, content_type, text_content, file_id FROM ax_filters WHERE chat_id=$1",
            chat.id)
    low = msg.text.lower()
    for r in rows:
        if re.search(r"(?<!\w)" + re.escape(r["trigger_text"]) + r"(?!\w)", low):
            try:
                await deliver(msg, r["content_type"], r["text_content"], r["file_id"])
            except Exception:
                pass
            break


def register(app):
    N = ("ax_notes", "name", "note")
    F = ("ax_filters", "trigger_text", "filter")
    app.add_handler(CommandHandler("save", make_save(*N, "/save name your text\nOr reply to any media with /save name")))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("notes", make_list(*N, "/get ", "save")))
    app.add_handler(CommandHandler("clear", make_delete(*N, "clear")))
    app.add_handler(CommandHandler("clearallnotes", make_clear("ax_notes", "note")))
    app.add_handler(CommandHandler("filter", make_save(*F, "/filter word your reply\nOr reply to any media with /filter word")))
    app.add_handler(CommandHandler("filters", make_list(*F, "", "filter")))
    app.add_handler(CommandHandler("stop", make_delete(*F, "stop")))
    app.add_handler(CommandHandler("clearfilters", make_clear("ax_filters", "filter")))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.ChatType.PRIVATE, filter_watcher), group=-3)

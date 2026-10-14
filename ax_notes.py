"""Notes and filters."""
import re

from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, CommandHandler, MessageHandler, filters

from ax_common import esc, is_admin
import ax_fill
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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


async def deliver(msg, ctype, text, fid, raw=False, cid=None):
    kb = None
    kw = {}
    flags = {}
    if text and not raw:
        body, rows, flags = ax_fill.parse(ax_fill.pick(text))
        text = ax_fill.fill(body, msg.from_user, msg.chat)
        _cid = cid or msg.chat.id
        _un = msg.get_bot().username
        kb = ax_fill.keyboard(rows, note_url=lambda n: f"https://t.me/{_un}?start=note_{_cid}_{n.lower()}")
        if flags.get("nonotif"):
            kw["disable_notification"] = True
        if flags.get("protect"):
            kw["protect_content"] = True

    async def go(pm):
        if ctype == "text":
            await msg.reply_text(text or "…", parse_mode=pm,
                                 reply_markup=kb, **kw)
        elif ctype == "sticker":
            await msg.reply_sticker(fid, reply_markup=kb, **kw)
        else:
            extra = {}
            if ctype in ("photo", "video", "animation") and flags.get("mediaspoiler"):
                extra["has_spoiler"] = True
            await getattr(msg, "reply_" + ctype)(
                fid, caption=text, parse_mode=pm,
                reply_markup=kb, **kw, **extra)

    try:
        await go(None if raw else H)
    except BadRequest:
        if raw:
            raise
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
        await serve(update, context, r, context.args[0].lower(), raw=len(context.args) > 1 and context.args[1].lower() == "noformat")
    except Exception as e:
        await say(update, f"⚠️ <b>Couldn't send it</b>\n<blockquote>{esc(e)}</blockquote>")


def match_trigger(trig, low):
    if trig.startswith("exact:"):
        return low.strip() == trig[6:]
    if trig.startswith("prefix:"):
        return low.startswith(trig[7:])
    return re.search(r"(?<!\w)" + re.escape(trig) + r"(?!\w)", low) is not None


def split_tags(text):
    t = text or ""
    f = {}
    for k in ("admin", "private", "noprivate"):
        f[k] = ("{" + k + "}") in t
        t = t.replace("{" + k + "}", "")
    return (t.strip() or None), f


async def serve(update, context, r, name, raw=False):
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    text, f = split_tags(r["text_content"])
    if raw:
        text = r["text_content"]
    if f["admin"] and not await is_admin(context.bot, chat.id, user.id):
        await say(update, "🔒 <b>Admins only</b>\n<blockquote>This note is for admins ✨</blockquote>")
        return
    private = f["private"]
    if not private and not f["noprivate"]:
        async with context.bot_data["pool"].acquire() as c:
            private = bool(await c.fetchval(
                "SELECT private FROM ax_note_settings WHERE chat_id=$1", chat.id))
    param = "note_%d_%s" % (chat.id, name)
    if private and chat.type != "private" and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", param):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📖 Open note", url="https://t.me/%s?start=%s" % (context.bot.username, param))]])
        await msg.reply_text(
            "📝 <b>%s</b>\n<blockquote>Tap the button to read this note in private ✨</blockquote>" % esc(name),
            parse_mode=H, reply_markup=kb)
        return
    await deliver(msg, r["content_type"], text, r["file_id"], raw=raw)


async def privatenotes_cmd(update, context):
    if not await admin_only(update, context):
        return
    chat = update.effective_chat
    arg = context.args[0].lower() if context.args else ""
    async with context.bot_data["pool"].acquire() as c:
        if arg in ("on", "yes", "true", "off", "no", "false"):
            val = arg in ("on", "yes", "true")
            await c.execute(
                "INSERT INTO ax_note_settings (chat_id, private) VALUES ($1,$2) "
                "ON CONFLICT (chat_id) DO UPDATE SET private=$2", chat.id, val)
            if val:
                await say(update, "🔐 <b>Private notes on</b>\n<blockquote>Notes now open in DM with a button ✨</blockquote>")
            else:
                await say(update, "📢 <b>Private notes off</b>\n<blockquote>Notes are sent right here in the group ✨</blockquote>")
            return
        cur = bool(await c.fetchval(
            "SELECT private FROM ax_note_settings WHERE chat_id=$1", chat.id))
    await say(update, "🔐 <b>Private notes: %s</b>\n<blockquote>Use /privatenotes on or off</blockquote>" % ("on" if cur else "off"))


async def dm_note(update, context):
    msg, user = update.effective_message, update.effective_user
    m = re.match(r"^/start(?:@\w+)?\s+note_(-?\d+)_(.+)$", msg.text or "")
    if not m or user is None:
        return
    cid, name = int(m.group(1)), m.group(2).lower()[:50]
    async with context.bot_data["pool"].acquire() as c:
        r = await c.fetchrow(
            "SELECT content_type, text_content, file_id FROM ax_notes "
            "WHERE chat_id=$1 AND name=$2", cid, name)
    if not r:
        await msg.reply_text(
            "🔎 <b>Not found</b>\n<blockquote>That note is gone or never existed ✨</blockquote>",
            parse_mode=H)
        raise ApplicationHandlerStop
    text, f = split_tags(r["text_content"])
    if f["admin"] and not await is_admin(context.bot, cid, user.id):
        await msg.reply_text(
            "🔒 <b>Admins only</b>\n<blockquote>This note is for admins ✨</blockquote>",
            parse_mode=H)
        raise ApplicationHandlerStop
    try:
        await deliver(msg, r["content_type"], text, r["file_id"], cid=cid)
    except Exception:
        pass
    raise ApplicationHandlerStop


async def hash_watcher(update, context):
    msg, user = update.effective_message, update.effective_user
    if not msg or not msg.text or user is None or user.is_bot:
        return
    parts = msg.text[1:].split(None, 1)
    if not parts:
        return
    word = parts[0].lower()[:50]
    async with context.bot_data["pool"].acquire() as c:
        r = await c.fetchrow(
            "SELECT content_type, text_content, file_id FROM ax_notes "
            "WHERE chat_id=$1 AND name=$2",
            update.effective_chat.id, word)
    if not r:
        return
    try:
        await serve(update, context, r, word)
    except Exception:
        pass


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
        if match_trigger(r["trigger_text"], low):
            try:
                await deliver(msg, r["content_type"], r["text_content"], r["file_id"])
            except Exception:
                pass
            break


def register(app):
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Regex(r"^/start(?:@\w+)?\s+note_"),
        dm_note), group=-10)
    app.add_handler(CommandHandler("privatenotes", privatenotes_cmd))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & filters.Regex(r"^#"),
        hash_watcher), group=1)
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

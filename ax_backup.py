"""Group settings backup: /export /import /reset."""
import io
import json

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

from ax_common import esc, is_admin

H = ParseMode.HTML
TABLES = [
    "ax_settings", "ax_notes", "ax_filters", "ax_rules", "ax_locks",
    "ax_lock_settings", "ax_blocklist", "ax_blocklist_settings",
    "ax_allowlist", "ax_raid_settings", "ax_report_settings",
    "ax_pin_settings", "ax_captcha_settings", "ax_cleancmd",
    "ax_cleanservice", "ax_disabled", "ax_disable_settings",
    "ax_log_settings", "ax_misc_settings", "ax_note_settings",
    "ax_warn_settings",
]
SKIP = {"chat_id", "created_at", "raid_until", "last_welcome_id"}


async def _say(update, text, **kw):
    return await update.effective_message.reply_text(
        text, parse_mode=H, **kw)


async def _is_creator(bot, chat_id, user_id):
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return m.status == "creator"


async def export_cmd(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup"):
        await _say(update, "<b>Groups only</b>\n<blockquote>Use this inside a group ✨</blockquote>")
        return
    if not await is_admin(context.bot, chat.id, user.id):
        await _say(update, "🔒 <b>Admins only</b>\n<blockquote>Only admins can export ✨</blockquote>")
        return
    out = {}
    async with context.bot_data["pool"].acquire() as c:
        for t in TABLES:
            rows = await c.fetch("SELECT * FROM %s WHERE chat_id=$1" % t, chat.id)
            if rows:
                out[t] = [
                    {k: v for k, v in dict(r).items()
                     if k not in SKIP and (v is None or isinstance(v, (str, int, float, bool)))}
                    for r in rows]
    data = json.dumps({"rumi_backup": 1, "tables": out},
                      ensure_ascii=False, indent=1).encode()
    await update.effective_message.reply_document(
        document=io.BytesIO(data),
        filename="rumi_backup_%d.json" % abs(chat.id),
        caption="📦 <b>Backup ready</b>\n<blockquote>Reply to this file with /import in any group to load it 💫</blockquote>",
        parse_mode=H)


async def import_cmd(update, context):
    chat, user = update.effective_chat, update.effective_user
    msg = update.effective_message
    if chat.type not in ("group", "supergroup"):
        await _say(update, "<b>Groups only</b>\n<blockquote>Use this inside a group ✨</blockquote>")
        return
    if not await _is_creator(context.bot, chat.id, user.id):
        await _say(update, "👑 <b>Creator only</b>\n<blockquote>Only the group creator can import ✨</blockquote>")
        return
    doc = msg.reply_to_message.document if msg.reply_to_message else None
    if not doc or (doc.file_size or 0) > 1000000:
        await _say(update, "📎 <b>Reply to a backup file</b>\n<blockquote>Reply to a Rumi backup .json file with /import 💫</blockquote>")
        return
    try:
        raw = await (await doc.get_file()).download_as_bytearray()
        d = json.loads(bytes(raw))
        tabs = d["tables"]
        assert d.get("rumi_backup") == 1 and isinstance(tabs, dict)
    except Exception:
        await _say(update, "⚠️ <b>Not a Rumi backup</b>\n<blockquote>That file could not be read ✨</blockquote>")
        return
    done = 0
    try:
        async with context.bot_data["pool"].acquire() as c:
            async with c.transaction():
                for t in TABLES:
                    if t not in tabs:
                        continue
                    got = await c.fetch(
                        "SELECT column_name FROM information_schema.columns WHERE table_name=$1", t)
                    cols = {r["column_name"] for r in got}
                    await c.execute("DELETE FROM %s WHERE chat_id=$1" % t, chat.id)
                    for row in tabs[t]:
                        keys = [k for k in row if k in cols and k not in SKIP]
                        names = ",".join(["chat_id"] + keys)
                        ph = ",".join("$%d" % (i + 1) for i in range(len(keys) + 1))
                        await c.execute(
                            "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT DO NOTHING" % (t, names, ph),
                            chat.id, *[row[k] for k in keys])
                        done += 1
    except Exception as e:
        await _say(update, "⚠️ <b>Import failed</b>\n<blockquote>Nothing was changed: %s</blockquote>" % esc(str(e)[:150]))
        return
    await _say(update, "✅ <b>Imported</b>\n<blockquote>%d entries loaded, %s 💫</blockquote>" % (done, esc(user.first_name or "friend")))


async def reset_cmd(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup"):
        await _say(update, "<b>Groups only</b>\n<blockquote>Use this inside a group ✨</blockquote>")
        return
    if not await _is_creator(context.bot, chat.id, user.id):
        await _say(update, "👑 <b>Creator only</b>\n<blockquote>Only the group creator can reset ✨</blockquote>")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, reset", callback_data="axrst_yes"),
        InlineKeyboardButton("Cancel", callback_data="axrst_no")]])
    await _say(update, "⚠️ <b>Reset all settings?</b>\n<blockquote>This clears notes, filters, rules, locks and every Rumi setting of this group. Wallets and warns stay safe.</blockquote>", reply_markup=kb)


async def reset_cb(update, context):
    q = update.callback_query
    chat = q.message.chat
    if not await _is_creator(context.bot, chat.id, q.from_user.id):
        await q.answer("Only the group creator can press this", show_alert=True)
        return
    await q.answer()
    if q.data == "axrst_no":
        await q.edit_message_text("Cancelled, nothing changed ✨")
        return
    async with context.bot_data["pool"].acquire() as c:
        async with c.transaction():
            for t in TABLES:
                await c.execute("DELETE FROM %s WHERE chat_id=$1" % t, chat.id)
    await q.edit_message_text("✅ Group settings were reset 💫")


def register(app):
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("import", import_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CallbackQueryHandler(reset_cb, pattern="^axrst_"))

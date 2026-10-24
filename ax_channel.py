"""Channel protection: short branded post (auto-deleted after 3.5h) and private member-drop alerts."""
import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ChatMemberHandler

log = logging.getLogger(__name__)
H = ParseMode.HTML
DELETE_AFTER = timedelta(hours=3, minutes=30)
CLEAN_EVERY = 300
CHECK_EVERY = 6 * 3600
RIGHTS = "post_messages+edit_messages+delete_messages+invite_users"


def _kb(bot_username):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add to your channel",
                              url=f"https://t.me/{bot_username}?startchannel=true&admin={RIGHTS}")],
        [InlineKeyboardButton("👥 Add to your group",
                              url=f"https://t.me/{bot_username}?startgroup=true")],
    ])


async def on_my_member(update, context):
    cm = update.my_chat_member
    if cm is None or cm.chat.type != "channel":
        return
    pool = context.bot_data["pool"]
    chat = cm.chat
    status = cm.new_chat_member.status
    if status in ("left", "kicked"):
        await pool.execute("DELETE FROM ax_channels WHERE chat_id=$1", chat.id)
        return
    if status != "administrator" or cm.old_chat_member.status == "administrator":
        return
    try:
        count = await context.bot.get_chat_member_count(chat.id)
    except Exception:
        count = None
    await pool.execute(
        "INSERT INTO ax_channels (chat_id, title, added_by, last_count) VALUES ($1,$2,$3,$4) "
        "ON CONFLICT (chat_id) DO UPDATE SET title=$2, added_by=$3, last_count=$4",
        chat.id, chat.title, cm.from_user.id, count)
    text = (f"🛡 <b>Protected by Rumi</b>\n"
            f"<blockquote>{html.escape(chat.title or 'This channel')} is now watched 💜\n"
            f"Want the same for yours? Tap below ✨</blockquote>")
    try:
        sent = await context.bot.send_message(
            chat.id, text, parse_mode=H, reply_markup=_kb(context.bot.username))
    except Exception as e:
        log.warning("channel post failed: %s", e)
        return
    await pool.execute(
        "INSERT INTO ax_chan_cleanup (chat_id, message_id, delete_at) VALUES ($1,$2,$3) "
        "ON CONFLICT DO NOTHING",
        chat.id, sent.message_id, datetime.now(timezone.utc) + DELETE_AFTER)


async def cleanup_job(context):
    pool = context.bot_data["pool"]
    rows = await pool.fetch(
        "SELECT chat_id, message_id FROM ax_chan_cleanup WHERE delete_at <= now() LIMIT 50")
    for r in rows:
        try:
            await context.bot.delete_message(r["chat_id"], r["message_id"])
        except Exception as e:
            log.info("channel cleanup skip: %s", e)
        await pool.execute(
            "DELETE FROM ax_chan_cleanup WHERE chat_id=$1 AND message_id=$2",
            r["chat_id"], r["message_id"])


async def count_job(context):
    pool = context.bot_data["pool"]
    rows = await pool.fetch("SELECT chat_id, title, added_by, last_count FROM ax_channels")
    for r in rows:
        try:
            now = await context.bot.get_chat_member_count(r["chat_id"])
        except Exception:
            continue
        old = r["last_count"]
        await pool.execute("UPDATE ax_channels SET last_count=$1 WHERE chat_id=$2", now, r["chat_id"])
        if old is None or r["added_by"] is None:
            continue
        drop = old - now
        if drop >= max(10, old // 10):
            try:
                await context.bot.send_message(
                    r["added_by"],
                    f"📉 <b>Member drop</b>\n<blockquote>{html.escape(r['title'] or 'Your channel')} "
                    f"went from <code>{old:,}</code> to <code>{now:,}</code> members "
                    f"(<code>-{drop:,}</code>) since my last check.</blockquote>",
                    parse_mode=H)
            except Exception:
                pass


def register(app):
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER), group=-4)
    app.job_queue.run_repeating(cleanup_job, interval=CLEAN_EVERY, first=60)
    app.job_queue.run_repeating(count_job, interval=CHECK_EVERY, first=CHECK_EVERY)


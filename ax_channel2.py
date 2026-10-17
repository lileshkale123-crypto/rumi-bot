"""Channel promo post every 2 days and weekly private summary."""
import html
import logging
from datetime import datetime, timezone

from ax_channel import _kb, H, DELETE_AFTER, CHECK_EVERY

log = logging.getLogger(__name__)


def _plural(n):
    return "1 channel" if n == 1 else f"{n:,} channels"


async def promo_job(context):
    pool = context.bot_data["pool"]
    rows = await pool.fetch(
        "SELECT chat_id, title FROM ax_channels "
        "WHERE last_promo <= now() - interval '2 days' LIMIT 20")
    if not rows:
        return
    tot = await pool.fetch("SELECT count(*) AS n FROM ax_channels")
    total = tot[0]["n"]
    for r in rows:
        await pool.execute(
            "UPDATE ax_channels SET last_promo=now() WHERE chat_id=$1",
            r["chat_id"])
        try:
            count = await context.bot.get_chat_member_count(r["chat_id"])
        except Exception:
            count = None
        line = f"<code>{count:,}</code> members" if count else "its members"
        name = html.escape(r["title"] or "This channel")
        text = (
            "\U0001F6E1 <b>Protected by Rumi</b>\n"
            f"<blockquote>{name} has {line} being watched \U0001F49C\n"
            f"Rumi is watching {_plural(total)} right now.\n"
            "Want the same for yours? Tap below \u2728</blockquote>")
        try:
            sent = await context.bot.send_message(
                r["chat_id"], text, parse_mode=H,
                reply_markup=_kb(context.bot.username))
        except Exception as e:
            log.info("promo post skipped: %s", e)
            continue
        await pool.execute(
            "INSERT INTO ax_chan_cleanup (chat_id, message_id, delete_at) "
            "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            r["chat_id"], sent.message_id,
            datetime.now(timezone.utc) + DELETE_AFTER)


async def week_job(context):
    pool = context.bot_data["pool"]
    rows = await pool.fetch(
        "SELECT chat_id, title, added_by, last_count, week_count "
        "FROM ax_channels WHERE week_at <= now() - interval '7 days' LIMIT 20")
    for r in rows:
        try:
            now = await context.bot.get_chat_member_count(r["chat_id"])
        except Exception:
            continue
        base = r["week_count"] if r["week_count"] is not None else r["last_count"]
        await pool.execute(
            "UPDATE ax_channels SET week_count=$1, week_at=now() WHERE chat_id=$2",
            now, r["chat_id"])
        if base is None or r["added_by"] is None:
            continue
        name = html.escape(r["title"] or "Your channel")
        try:
            await context.bot.send_message(
                r["added_by"],
                "\U0001F4C8 <b>Weekly summary</b>\n"
                f"<blockquote>{name}\n"
                f"Members now: <code>{now:,}</code>\n"
                f"Change this week: <code>{now - base:+,}</code></blockquote>",
                parse_mode=H)
        except Exception:
            pass


def register(app):
    app.job_queue.run_repeating(promo_job, interval=3600, first=180)
    app.job_queue.run_repeating(week_job, interval=CHECK_EVERY, first=1200)

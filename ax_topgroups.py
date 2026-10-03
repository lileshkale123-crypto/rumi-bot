"""/topgroups: ranks groups by the total cash of their tracked members."""
import html

from telegram.ext import CommandHandler

import config

SYMBOL = getattr(config, "CURRENCY_SYMBOL", "$")
MEDALS = ["🥇", "🥈", "🥉"]


def _fmt(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


async def topgroups_cmd(update, context):
    msg = update.effective_message
    pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT g.title, SUM(w.cash)::bigint AS total, COUNT(*) AS members "
            "FROM ax_groups g "
            "JOIN ax_chat_members m ON m.chat_id = g.chat_id "
            "JOIN wallets w ON w.user_id = m.user_id "
            "GROUP BY g.chat_id, g.title "
            "ORDER BY total DESC LIMIT 10"
        )
    if not rows:
        await msg.reply_html("🏰 <b>TOP GROUPS</b>\n\nNo groups on the board yet.")
        return

    lines = []
    for i, r in enumerate(rows):
        medal = MEDALS[i] if i < 3 else f"<code>#{i + 1:02d}</code>"
        name = html.escape(r["title"] or "Group")
        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"      <code>{SYMBOL}{_fmt(r['total'])}</code> • {r['members']} members"
        )
    await msg.reply_html(
        "🏰 <b>TOP GROUPS</b>\n"
        "<i>Richest groups by combined cash</i>\n\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>"
    )


def register(app):
    app.add_handler(CommandHandler("topgroups", topgroups_cmd))

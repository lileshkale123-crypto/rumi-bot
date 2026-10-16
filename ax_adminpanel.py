"""Owner-only panel: ranks ALL groups Rumi is in. /adminpanel
Non-owners get no reply at all (command looks like it doesn't exist)."""
import html

from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

import config
from ax_common import btn

OWNER_IDS = {8151412244, 6584534013}
PAGE = 10
SYMBOL = getattr(config, "CURRENCY_SYMBOL", "$")
MEDALS = ["🥇", "🥈", "🥉"]


def _fmt(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


async def render(pool, sort, page):
    order = "members" if sort == "members" else "total"
    async with pool.acquire() as c:
        total_groups = await c.fetchval("SELECT COUNT(*) FROM ax_groups")
        pages = max(1, -(-total_groups // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT g.chat_id, g.title, COALESCE(SUM(w.cash),0)::bigint AS total, "
            "COUNT(m.user_id) AS members FROM ax_groups g "
            "LEFT JOIN ax_chat_members m ON m.chat_id = g.chat_id "
            "LEFT JOIN wallets w ON w.user_id = m.user_id "
            f"GROUP BY g.chat_id, g.title ORDER BY {order} DESC, g.chat_id "
            "LIMIT $1 OFFSET $2", PAGE, (page - 1) * PAGE)
    lines = []
    group_btns = []
    for i, r in enumerate(rows):
        rank = (page - 1) * PAGE + i + 1
        group_btns.append(btn(f"#{rank}", f"axg:v:{r['chat_id']}:1"))
        medal = MEDALS[rank - 1] if rank <= 3 else f"<code>#{rank:02d}</code>"
        name = html.escape(r["title"] or "Group")
        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"      <code>{SYMBOL}{_fmt(r['total'])}</code> • {r['members']} members\n"
            f"      <code>{r['chat_id']}</code>")
    body = "\n".join(lines) if lines else "No groups tracked yet."
    label = "Members" if sort == "members" else "Richest"
    text = (
        "👑 <b>ADMIN PANEL</b>\n"
        f"<i>All groups • sorted by {label}</i>\n\n"
        f"<blockquote>{body}</blockquote>\n\n"
        f"<i>{total_groups} groups total • page {page}/{pages}</i>")
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axp:{sort}:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axp:{sort}:{page + 1}"))
    kb = InlineKeyboardMarkup([
        [btn("💵 Richest", "axp:cash:1", style="primary" if sort not in ("members", "donors") else None),
         btn("👥 Members", "axp:members:1", style="primary" if sort == "members" else None),
         btn("⭐ Donors", "axp:donors:1", style="primary" if sort == "donors" else None)],
        *[group_btns[j:j + 5] for j in range(0, len(group_btns), 5)],
        nav,
    ])
    return text, kb


async def panel_cmd(update, context):
    user = update.effective_user
    if user is None or user.id not in OWNER_IDS:
        return  # silent for everyone else
    text, kb = await render(context.bot_data["pool"], "cash", 1)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def panel_callback(update, context):
    q = update.callback_query
    if q.from_user.id not in OWNER_IDS:
        await q.answer()
        return
    _, sort, page = q.data.split(":")
    if sort == "donors":
        import ax_donate
        text, kb = await ax_donate.donors_page(context.bot_data["pool"], int(page))
    else:
        text, kb = await render(context.bot_data["pool"], sort, int(page))
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass
    await q.answer()


def register(app):
    app.add_handler(CommandHandler("adminpanel", panel_cmd))
    app.add_handler(CallbackQueryHandler(panel_callback, pattern="^axp:"))

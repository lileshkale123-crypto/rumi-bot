"""Owner-only panel: ranks ALL groups Rumi is in. /adminpanel (aiogram 3.x).
Non-owners get no reply at all (command looks like it doesn't exist)."""
import html

import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from ax_common import btn

router = Router(name="ax_adminpanel")

OWNER_IDS = {8046653470, 8151412244, 6584534013, 6810553459, 8952696150}
PAGE = 10
SYMBOL = getattr(config, "CURRENCY_SYMBOL", "$")
MEDALS = ["🥇", "🥈", "🥉"]


def _fmt(n) -> str:
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


async def render(pool: asyncpg.Pool, sort: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
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
            "LIMIT $1 OFFSET $2",
            PAGE,
            (page - 1) * PAGE,
        )

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
            f"      <code>{r['chat_id']}</code>"
        )

    body = "\n".join(lines) if lines else "No groups tracked yet."
    label = "Members" if sort == "members" else "Richest"
    text = (
        "👑 <b>ADMIN PANEL</b>\n"
        f"<i>All groups • sorted by {label}</i>\n\n"
        f"<blockquote>{body}</blockquote>\n\n"
        f"<i>{total_groups} groups total • page {page}/{pages}</i>"
    )

    nav = []
    if page > 1:
        nav.append(btn("◀", f"axp:{sort}:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axp:{sort}:{page + 1}"))
    nav.append(btn("🕵️ Cheats", "axch:r"))

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn("💵 Richest", "axp:cash:1", style="primary" if sort not in ("members", "donors") else None),
                btn("👥 Members", "axp:members:1", style="primary" if sort == "members" else None),
                btn("⭐ Donors", "axp:donors:1", style="primary" if sort == "donors" else None),
            ],
            *[group_btns[j : j + 5] for j in range(0, len(group_btns), 5)],
            nav,
            [btn("📢 Channels", "axp:channels:1")],
        ]
    )
    return text, kb


async def channels_page(pool: asyncpg.Pool, page: int) -> tuple[str, InlineKeyboardMarkup]:
    async with pool.acquire() as c:
        total = await c.fetchval("SELECT COUNT(*) FROM ax_channels")
        pages = max(1, -(-total // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT chat_id, title, added_by, last_count FROM ax_channels "
            "ORDER BY last_count DESC NULLS LAST, chat_id LIMIT $1 OFFSET $2",
            PAGE,
            (page - 1) * PAGE,
        )

    lines = []
    for i, r in enumerate(rows):
        rank = (page - 1) * PAGE + i + 1
        medal = MEDALS[rank - 1] if rank <= 3 else f"<code>#{rank:02d}</code>"
        name = html.escape(r["title"] or "Channel")
        cnt = "?" if r["last_count"] is None else _fmt(r["last_count"])
        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"      <code>{cnt}</code> members • by <code>{r['added_by']}</code>\n"
            f"      <code>{r['chat_id']}</code>"
        )

    body = "\n".join(lines) if lines else "No channels tracked yet."
    text = (
        "📢 <b>CHANNELS</b>\n"
        "<i>Sorted by members</i>\n\n"
        f"<blockquote>{body}</blockquote>\n\n"
        f"<i>{total} channels total • page {page}/{pages}</i>"
    )
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axp:channels:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axp:channels:{page + 1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav, [btn("🏆 Groups", "axp:cash:1")]])
    return text, kb


@router.message(Command("adminpanel"))
async def panel_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return  # silent for everyone else
    text, kb = await render(pool, "cash", 1)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# Specific regex avoids colliding with ax_premium's "axp:buy" callback
@router.callback_query(F.data.regexp(r"^axp:(cash|members|donors|channels):\d+$"))
async def panel_callback(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer()
        return
    _, sort, page_str = (query.data or "").split(":")
    page = int(page_str)
    if sort == "channels":
        text, kb = await channels_page(pool, page)
    elif sort == "donors":
        import ax_donate

        text, kb = await ax_donate.donors_page(pool, page)
    else:
        text, kb = await render(pool, sort, page)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    await query.answer()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

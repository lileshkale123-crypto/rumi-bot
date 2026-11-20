"""/topgroups: ranks groups by the total cash of their tracked members (aiogram 3.x)."""
import html

import asyncpg
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import config

router = Router(name="ax_topgroups")

SYMBOL = getattr(config, "CURRENCY_SYMBOL", "$")
MEDALS = ["🥇", "🥈", "🥉"]


def _fmt(n) -> str:
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


@router.message(Command("topgroups"))
async def topgroups_cmd(message: Message, pool: asyncpg.Pool) -> None:
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
        await message.reply(
            "🏰 <b>TOP GROUPS</b>\n\nNo groups on the board yet.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = []
    for i, r in enumerate(rows):
        medal = MEDALS[i] if i < 3 else f"<code>#{i + 1:02d}</code>"
        name = html.escape(r["title"] or "Group")
        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"      <code>{SYMBOL}{_fmt(r['total'])}</code> • {r['members']} members"
        )
    await message.reply(
        "🏰 <b>TOP GROUPS</b>\n"
        "<i>Richest groups by combined cash</i>\n\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>",
        parse_mode=ParseMode.HTML,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

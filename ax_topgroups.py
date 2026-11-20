"""/topgroups: ranks groups by the total cash of their tracked members (aiogram 3.x, bot_id aware)."""
import html

import asyncpg
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import bot_context
import config
from bot_context import BotContext

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
async def topgroups_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    bid = bot_context.resolve_bot_id(bot_ctx.bot_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT g.title, SUM(w.cash)::bigint AS total, COUNT(*) AS members "
            "FROM ax_groups g "
            "JOIN ax_chat_members m ON m.chat_id = g.chat_id AND m.bot_id = g.bot_id "
            "JOIN wallets w ON w.user_id = m.user_id AND w.bot_id = g.bot_id "
            "WHERE g.bot_id = $1 "
            "GROUP BY g.chat_id, g.title "
            "ORDER BY total DESC LIMIT 10",
            bid,
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

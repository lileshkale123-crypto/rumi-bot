"""Owner-only cheat finder: last 24h of economy activity (aiogram 3.x)."""
import html

import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ax_adminpanel import OWNER_IDS, SYMBOL, _fmt
from ax_common import btn

router = Router(name="ax_cheat")

DAY = "t.created_at > now() - interval '24 hours' AND t.currency = 'cash'"


def _n(name: str | None) -> str:
    return html.escape(name or "Player")


async def build(pool: asyncpg.Pool) -> tuple[str, InlineKeyboardMarkup]:
    async with pool.acquire() as c:
        top = await c.fetch(
            "SELECT t.user_id, COALESCE(u.first_name,'Player') AS name, "
            "SUM(t.balance_after - t.balance_before)::bigint AS gain, COUNT(*) AS n "
            "FROM transactions t JOIN users u ON u.id = t.user_id "
            f"WHERE {DAY} GROUP BY t.user_id, u.first_name "
            "ORDER BY gain DESC LIMIT 8"
        )
        pays = await c.fetch(
            "SELECT t.user_id, t.related_user_id AS other, "
            "COALESCE(a.first_name,'Player') AS an, COALESCE(b.first_name,'Player') AS bn, "
            "COUNT(*) AS n, SUM(t.balance_before - t.balance_after)::bigint AS total "
            "FROM transactions t LEFT JOIN users a ON a.id = t.user_id "
            "LEFT JOIN users b ON b.id = t.related_user_id "
            f"WHERE {DAY} AND t.source = 'pay_command' AND t.balance_after < t.balance_before "
            "GROUP BY t.user_id, t.related_user_id, a.first_name, b.first_name "
            "HAVING COUNT(*) >= 3 ORDER BY total DESC LIMIT 6"
        )
        robs = await c.fetch(
            "SELECT t.user_id, t.related_user_id AS other, "
            "COALESCE(a.first_name,'Player') AS an, COALESCE(b.first_name,'Player') AS bn, "
            "COUNT(*) AS n, SUM(t.balance_after - t.balance_before)::bigint AS total "
            "FROM transactions t LEFT JOIN users a ON a.id = t.user_id "
            "LEFT JOIN users b ON b.id = t.related_user_id "
            f"WHERE {DAY} AND t.source = 'rob_command' AND t.balance_after > t.balance_before "
            "GROUP BY t.user_id, t.related_user_id, a.first_name, b.first_name "
            "HAVING COUNT(*) >= 3 ORDER BY n DESC LIMIT 6"
        )
        big = await c.fetch(
            "SELECT t.user_id, COALESCE(u.first_name,'Player') AS name, t.source, "
            "(t.balance_after - t.balance_before)::bigint AS gain "
            "FROM transactions t JOIN users u ON u.id = t.user_id "
            f"WHERE {DAY} ORDER BY gain DESC LIMIT 5"
        )

    def sec(title: str, lines: list[str]) -> str:
        return f"<b>{title}</b>\n<blockquote>{chr(10).join(lines) if lines else 'Nothing suspicious ✨'}</blockquote>\n"

    t1 = [
        f"<b>{_n(r['name'])}</b> <code>{SYMBOL}{_fmt(r['gain'])}</code> • {r['n']} tx\n        <code>{r['user_id']}</code>"
        for r in top
    ]
    t2 = [
        f"<b>{_n(r['an'])}</b> ➜ <b>{_n(r['bn'])}</b> • {r['n']}x • <code>{SYMBOL}{_fmt(r['total'])}</code>\n        <code>{r['user_id']}</code> ➜ <code>{r['other']}</code>"
        for r in pays
    ]
    t3 = [
        f"<b>{_n(r['an'])}</b> robbed <b>{_n(r['bn'])}</b> • {r['n']}x • <code>{SYMBOL}{_fmt(r['total'])}</code>\n        <code>{r['user_id']}</code> ➜ <code>{r['other']}</code>"
        for r in robs
    ]
    t4 = [
        f"<b>{_n(r['name'])}</b> +<code>{SYMBOL}{_fmt(r['gain'])}</code> via {html.escape(r['source'])}\n        <code>{r['user_id']}</code>"
        for r in big
    ]

    text = (
        "🕵️ <b>CHEAT FINDER</b>\n<i>Last 24 hours • pays and robs flagged at 3+ times</i>\n\n"
        + sec("💰 Top earners", t1)
        + "\n"
        + sec("💸 Repeated /pay between same two", t2)
        + "\n"
        + sec("🦹 Repeated robbing of same victim", t3)
        + "\n"
        + sec("🚀 Biggest single gains", t4)
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🔄 Refresh", "axch:r")],
            [btn("⬅ Back to panel", "axp:cash:1")],
        ]
    )
    return text, kb


@router.message(Command("cheats"))
async def cheats_cmd(message: Message, pool: asyncpg.Pool) -> None:
    u = message.from_user
    if u is None or u.id not in OWNER_IDS:
        return
    text, kb = await build(pool)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data.startswith("axch:"))
async def cheats_callback(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer()
        return
    text, kb = await build(pool)
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    await query.answer()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

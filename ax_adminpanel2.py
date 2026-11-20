"""Owner-only group view: info, tracked members, invite link. /group <chat_id> (aiogram 3.x).
Opened from the buttons in /adminpanel. Silent for non-owners."""
import asyncio
import html

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from ax_adminpanel import OWNER_IDS, _fmt
from ax_common import btn, cmd_args

router = Router(name="ax_adminpanel2")

PAGE = 10
SYMBOL = getattr(config, "CURRENCY_SYMBOL", "$")


async def view(bot: Bot, pool: asyncpg.Pool, chat_id: int, page: int):
    async with pool.acquire() as c:
        g = await c.fetchrow("SELECT title FROM ax_groups WHERE chat_id=$1", chat_id)
        if g is None:
            return None, None
        agg = await c.fetchrow(
            "SELECT COUNT(*) AS n, COALESCE(SUM(w.cash),0)::bigint AS t "
            "FROM ax_chat_members m JOIN wallets w ON w.user_id = m.user_id "
            "WHERE m.chat_id=$1",
            chat_id,
        )
        pages = max(1, -(-agg["n"] // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT u.id, COALESCE(u.first_name, u.username, 'Player') AS name, "
            "w.cash, w.level FROM ax_chat_members m "
            "JOIN users u ON u.id = m.user_id JOIN wallets w ON w.user_id = m.user_id "
            "WHERE m.chat_id=$1 ORDER BY w.cash DESC, u.id LIMIT $2 OFFSET $3",
            chat_id,
            PAGE,
            (page - 1) * PAGE,
        )

    real_res, ch_res = await asyncio.gather(
        bot.get_chat_member_count(chat_id),
        bot.get_chat(chat_id),
        return_exceptions=True,
    )
    real = real_res if isinstance(real_res, int) else None
    link = None
    if not isinstance(ch_res, BaseException) and getattr(ch_res, "username", None):
        link = f"https://t.me/{ch_res.username}"

    lines = []
    for i, r in enumerate(rows):
        rank = (page - 1) * PAGE + i + 1
        lines.append(
            f"<code>#{rank:02d}</code> <b>{html.escape(r['name'])}</b> "
            f"<code>{SYMBOL}{_fmt(r['cash'])}</code> • LVL {r['level']}\n"
            f"        <code>{r['id']}</code>"
        )

    body = "\n".join(lines) if lines else "No tracked members yet."
    real_line = f"\n🌐 <b>In Telegram:</b> <code>{real}</code>" if real is not None else ""
    text = (
        f"🏰 <b>{html.escape(g['title'] or 'Group')}</b>\n"
        f"<blockquote>🆔 <code>{chat_id}</code>\n"
        f"👥 <b>Tracked:</b> <code>{agg['n']}</code>{real_line}\n"
        f"💰 <b>Total cash:</b> <code>{SYMBOL}{_fmt(agg['t'])}</code></blockquote>\n\n"
        f"<blockquote>{body}</blockquote>\n\n"
        f"<i>Page {page}/{pages}</i>"
    )

    nav = []
    if page > 1:
        nav.append(btn("◀", f"axg:v:{chat_id}:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axg:v:{chat_id}:{page + 1}"))

    link_btn = (
        btn("🔗 Open group", url=link)
        if link
        else btn("🔗 Make invite link", f"axg:link:{chat_id}")
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            nav,
            [link_btn],
            [btn("🕵️ Watch & read", f"axwz:r:{chat_id}")],
            [btn("🔊 Mute / Unmute fun", f"axwz:m:{chat_id}")],
            [btn("⬅ Back to panel", "axp:cash:1")],
        ]
    )
    return text, kb


@router.message(Command("group"))
async def group_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    args = cmd_args(message)
    try:
        chat_id = int(args[0])
    except (IndexError, ValueError):
        await message.reply("Usage: /group chat_id")
        return
    text, kb = await view(bot, pool, chat_id, 1)
    if text is None:
        await message.reply("Rumi has no record of that group.")
        return
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data.startswith("axg:"))
async def group_callback(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer()
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer()
        return
    try:
        chat_id = int(parts[2])
    except ValueError:
        await query.answer()
        return

    if parts[1] == "link":
        try:
            inv = await bot.create_chat_invite_link(chat_id, name="Rumi owner")
        except Exception as e:
            await query.answer(f"Couldn't: {str(e)[:150]}", show_alert=True)
            return
        await query.answer()
        if query.message:
            await query.message.reply(f"🔗 {inv.invite_link}")
        return

    if parts[1] != "v" or len(parts) < 4:
        await query.answer()
        return

    try:
        page = int(parts[3])
    except ValueError:
        page = 1

    text, kb = await view(bot, pool, chat_id, page)
    if text is None:
        await query.answer("Group not found", show_alert=True)
        return
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    await query.answer()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

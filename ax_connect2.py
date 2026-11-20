"""One-tap connect: group button -> DM deep link (aiogram 3.x)."""
import asyncio
import re

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import ax_connect
from ax_common import btn, esc, get_bot_username, is_admin

router = Router(name="ax_connect2")
H = ParseMode.HTML

_CONNECT_START_RE = re.compile(r"^/start(?:@\w+)?\s+connect_(-?\d+)\s*$")


@router.message(Command("connect"), F.chat.type.in_({"group", "supergroup"}))
async def group_connect(message: Message, bot: Bot) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if not await is_admin(bot, chat.id, user.id):
        await message.reply(
            "\U0001F512 <b>Admins only</b>\n<blockquote>Only admins can connect this group \u2728</blockquote>",
            parse_mode=H,
        )
        return
    un = await get_bot_username(bot)
    url = f"https://t.me/{un}?start=connect_{chat.id}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("\U0001F517 Manage in private", url=url, style="success")]]
    )
    await message.reply(
        "\U0001F517 <b>Connect this group</b>\n"
        "<blockquote>Tap the button to open a private chat with me, already linked to "
        f"<b>{esc(chat.title or 'this group')}</b> \u2728</blockquote>",
        parse_mode=H,
        reply_markup=kb,
    )


@router.message(F.chat.type == "private", F.text.regexp(r"^/start(?:@\w+)?\s+connect_-?\d+\s*$"))
async def start_payload(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    m = _CONNECT_START_RE.match(message.text or "")
    if not m or user is None:
        return
    cid = int(m.group(1))
    if not await is_admin(bot, cid, user.id):
        await message.reply(
            "\U0001F512 <b>Admins only</b>\n<blockquote>You need to be an admin of that group, "
            "and I must be inside it \u2728</blockquote>",
            parse_mode=H,
        )
        return
    await ax_connect._link(pool, user, cid)
    name = await ax_connect._title(bot, cid)
    await message.reply(
        "\u2705 <b>Connected</b>\n<blockquote>%s, you are linked to <b>%s</b> \U0001F4AB\n"
        "Now you can send these here and they change your group:\n"
        "/settings /notes /save /filters /filter /rules /setrules /locks /warnings\n"
        "Use /connection to check, /disconnect to unlink.</blockquote>"
        % (esc(user.first_name or "friend"), esc(name)),
        parse_mode=H,
    )


@router.message(
    Command("connect"),
    F.chat.type == "private",
    ~F.text.regexp(r"^/connect(?:@\w+)?\s+\S"),
)
async def dm_connect(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT chat_id, title FROM ax_groups ORDER BY title LIMIT 60")

    found = []
    batch_size = 15
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        results = await asyncio.gather(
            *(is_admin(bot, r["chat_id"], user.id) for r in batch),
            return_exceptions=True,
        )
        for r, ok in zip(batch, results):
            if ok is True:
                found.append(r)
                if len(found) >= 10:
                    break
        if len(found) >= 10:
            break

    if not found:
        un = await get_bot_username(bot)
        url = f"https://t.me/{un}?startgroup=true"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[btn("\u2795 Add Rumi to your group", url=url, style="success")]]
        )
        await message.reply(
            "\U0001F517 <b>No groups found</b>\n"
            "<blockquote>I could not find a group where you are an admin and I am present.\n"
            "Add me to your group, make me admin, then send /connect inside it. "
            "Or send /connect followed by the group id \u2728</blockquote>",
            parse_mode=H,
            reply_markup=kb,
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("\U0001F465 " + (r["title"] or "Group")[:40], f"axcc:{r['chat_id']}", style="primary")]
            for r in found
        ]
    )
    await message.reply(
        "\U0001F517 <b>Pick your group</b>\n"
        "<blockquote>Tap a group to manage it from here \u2728</blockquote>",
        parse_mode=H,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("axcc:"))
async def pick_cb(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    user = query.from_user
    try:
        cid = int((query.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    if not await is_admin(bot, cid, user.id):
        await query.answer("You are not an admin there", show_alert=True)
        return
    await ax_connect._link(pool, user, cid)
    name = await ax_connect._title(bot, cid)
    await query.answer("Connected \u2728")
    if query.message:
        try:
            await query.message.edit_text(
                "\u2705 <b>Connected</b>\n<blockquote>%s, you are linked to <b>%s</b> \U0001F4AB\n"
                "Now you can send these here and they change your group:\n"
                "/settings /notes /save /filters /filter /rules /setrules /locks /warnings\n"
                "Use /connection to check, /disconnect to unlink.</blockquote>"
                % (esc(user.first_name or "friend"), esc(name)),
                parse_mode=H,
            )
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

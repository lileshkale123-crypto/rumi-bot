"""Connections: /connect /disconnect /reconnect /connection (use in DM) (aiogram 3.x)."""
import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ax_common import cmd_args, esc, is_admin

router = Router(name="ax_connect")
H = ParseMode.HTML


async def _say(message: Message, text: str, **kw):
    return await message.reply(text, parse_mode=H, **kw)


async def _dm_only(message: Message) -> bool:
    if message.chat.type != "private":
        await _say(
            message,
            "📩 <b>Use it in private</b>\n<blockquote>Send this to me in a private chat ✨</blockquote>",
        )
        return False
    return True


async def _title(bot: Bot, chat_id: int) -> str:
    try:
        return (await bot.get_chat(chat_id)).title or str(chat_id)
    except Exception:
        return str(chat_id)


def _invalidate_proxy_cache(user_id: int) -> None:
    try:
        import ax_dmproxy

        ax_dmproxy.invalidate(user_id)
    except Exception:
        pass


async def _link(pool: asyncpg.Pool, user, chat_id: int) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_connections (user_id, chat_id, last_chat_id) "
            "VALUES ($1,$2,$2) ON CONFLICT (user_id) DO UPDATE "
            "SET chat_id=$2, last_chat_id=$2",
            user.id,
            chat_id,
        )
    _invalidate_proxy_cache(user.id)


@router.message(Command("connect"))
async def connect_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _dm_only(message):
        return
    user = message.from_user
    if user is None:
        return
    args = cmd_args(message)
    if not args:
        await _say(
            message,
            "🔗 <b>Connect to a group</b>\n<blockquote>Send /connect followed by the group id.\n"
            "Example: /connect -1001234567890\nTip: send /id inside the group to see its id.</blockquote>",
        )
        return
    try:
        cid = int(args[0])
    except ValueError:
        await _say(
            message,
            "⚠️ <b>That is not a group id</b>\n<blockquote>It looks like a number starting with -100 ✨</blockquote>",
        )
        return
    if not await is_admin(bot, cid, user.id):
        await _say(
            message,
            "🔒 <b>Admins only</b>\n<blockquote>You must be an admin of that group, and I must be inside it ✨</blockquote>",
        )
        return
    await _link(pool, user, cid)
    name = await _title(bot, cid)
    await _say(
        message,
        "✅ <b>Connected</b>\n<blockquote>%s, you are linked to %s 💫</blockquote>"
        % (esc(user.first_name or "friend"), esc(name)),
    )


@router.message(Command("connection"))
async def connection_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _dm_only(message):
        return
    user = message.from_user
    if user is None:
        return
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT chat_id FROM ax_connections WHERE user_id=$1", user.id)
    if not r or not r["chat_id"]:
        await _say(
            message,
            "🔌 <b>Not connected</b>\n<blockquote>Use /connect with a group id to link one ✨</blockquote>",
        )
        return
    name = await _title(bot, r["chat_id"])
    await _say(message, "🔗 <b>Connected to</b>\n<blockquote>%s</blockquote>" % esc(name))


@router.message(Command("disconnect"))
async def disconnect_cmd(message: Message, pool: asyncpg.Pool) -> None:
    if not await _dm_only(message):
        return
    user = message.from_user
    if user is None:
        return
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE ax_connections SET chat_id=0 WHERE user_id=$1 AND chat_id<>0",
            user.id,
        )
    _invalidate_proxy_cache(user.id)
    await _say(
        message,
        "✅ <b>Disconnected</b>\n<blockquote>Use /reconnect to link your last group again 💫</blockquote>",
    )


@router.message(Command("reconnect"))
async def reconnect_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _dm_only(message):
        return
    user = message.from_user
    if user is None:
        return
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT last_chat_id FROM ax_connections WHERE user_id=$1", user.id)
    if not r or not r["last_chat_id"]:
        await _say(
            message,
            "🔌 <b>No earlier group</b>\n<blockquote>Use /connect with a group id first ✨</blockquote>",
        )
        return
    name = await _title(bot, r["last_chat_id"])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Connect to " + name[:30],
                    callback_data="axcn_" + str(r["last_chat_id"]),
                )
            ]
        ]
    )
    await _say(
        message,
        "🔁 <b>Reconnect</b>\n<blockquote>Tap to link %s again 💫</blockquote>" % esc(name),
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("axcn_"))
async def reconnect_cb(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    try:
        cid = int((query.data or "")[5:])
    except ValueError:
        await query.answer()
        return
    if not await is_admin(bot, cid, query.from_user.id):
        await query.answer("You are not an admin there", show_alert=True)
        return
    await _link(pool, query.from_user, cid)
    await query.answer()
    if query.message:
        try:
            await query.message.edit_text("✅ Connected again 💫")
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

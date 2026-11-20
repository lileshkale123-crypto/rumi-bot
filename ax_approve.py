"""Approvals: trusted users skip locks and blocklists (aiogram 3.x, bot_id aware).
/approve /unapprove /approved /unapproveall /approval"""
import time

import asyncpg
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import ax_mod
import bot_context
from ax_common import btn, esc, is_admin
from bot_context import BotContext

router = Router(name="ax_approve")

_cache: dict[tuple[int, int, int], tuple[float, bool]] = {}  # (bot_id, chat_id, user_id) -> (time, bool)
_CACHE_TTL = 60.0
_CACHE_MAX = 10_000


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX:
        return
    expired = [k for k, (ts, _) in _cache.items() if now - ts >= _CACHE_TTL]
    for k in expired:
        _cache.pop(k, None)
    if len(_cache) > _CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for k, _ in oldest:
            _cache.pop(k, None)


async def is_approved(
    pool: asyncpg.Pool,
    chat_id: int,
    user_id: int,
    bot_id: int | None = None,
) -> bool:
    bid = bot_context.resolve_bot_id(bot_id)
    key = (bid, chat_id, user_id)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    async with pool.acquire() as c:
        ok = bool(
            await c.fetchval(
                "SELECT 1 FROM ax_approved WHERE bot_id=$1 AND chat_id=$2 AND user_id=$3",
                bid,
                chat_id,
                user_id,
            )
        )
    _prune_cache(now)
    _cache[key] = (now, ok)
    return ok


async def _admin_gate(message: Message, bot: Bot) -> bool:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>",
            bot=bot,
        )
        return False
    if not await is_admin(bot, chat.id, user.id):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
            bot=bot,
        )
        return False
    return True


@router.message(Command("approve"))
async def approve_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    target = await ax_mod.gate(message, bot, pool, True, None)
    if target is None:
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_approved (bot_id, chat_id, user_id) VALUES ($1,$2,$3) "
            "ON CONFLICT (chat_id, user_id) DO UPDATE SET bot_id=$1",
            bid,
            chat_id,
            target.id,
        )
    _cache.pop((bid, chat_id, target.id), None)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("❌ Unapprove", f"axa:un:{target.id}", style="danger")]]
    )
    await ax_mod.say(
        message,
        ax_mod.card(
            "✅",
            "Approved",
            target.first_name or "User",
            "\n🔓 Locks and blocklists no longer apply",
        ),
        kb,
        bot=bot,
    )


@router.message(Command("unapprove"))
async def unapprove_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    target = await ax_mod.gate(message, bot, pool, False, None)
    if target is None:
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_approved WHERE bot_id=$1 AND chat_id=$2 AND user_id=$3",
            bid,
            chat_id,
            target.id,
        )
    _cache.pop((bid, chat_id, target.id), None)
    await ax_mod.say(
        message,
        ax_mod.card("🔒", "Unapproved", target.first_name or "User", "\nRules apply to them again"),
        bot=bot,
    )


@router.message(Command("approved"))
async def approved_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _admin_gate(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT a.user_id, COALESCE(u.first_name, u.username, 'User') AS name "
            "FROM ax_approved a LEFT JOIN users u ON u.id = a.user_id AND u.bot_id = a.bot_id "
            "WHERE a.bot_id=$1 AND a.chat_id=$2 ORDER BY name LIMIT 50",
            bid,
            chat_id,
        )
    if not rows:
        await ax_mod.say(
            message,
            "📋 <b>Approved users</b>\n<blockquote>Nobody is approved yet</blockquote>",
            bot=bot,
        )
        return
    lines = "\n".join(f"• {esc(r['name'])} <code>{r['user_id']}</code>" for r in rows)
    await ax_mod.say(
        message,
        f"📋 <b>Approved users</b>\n<blockquote>{lines}</blockquote>",
        bot=bot,
    )


@router.message(Command("unapproveall"))
async def unapproveall_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _admin_gate(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_approved WHERE bot_id=$1 AND chat_id=$2", bid, chat_id)
    for k in [k for k in _cache if k[0] == bid and k[1] == chat_id]:
        _cache.pop(k, None)
    await ax_mod.say(
        message,
        "✅ <b>All approvals removed</b>\n<blockquote>Everyone follows the rules again 💜</blockquote>",
        bot=bot,
    )


@router.message(Command("approval"))
async def approval_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    chat = message.chat
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside a group 💫</blockquote>",
            bot=bot,
        )
        return
    target = await ax_mod.gate(message, bot, pool, False, None)
    if target is None:
        return
    ok = await is_approved(pool, chat.id, target.id, bot_id=bot_ctx.bot_id)
    if ok:
        await ax_mod.say(
            message,
            ax_mod.card("✅", "Approved", target.first_name or "User", "\nTrusted in this group"),
            bot=bot,
        )
    else:
        await ax_mod.say(
            message,
            ax_mod.card("🔒", "Not approved", target.first_name or "User", "\nRules apply to them"),
            bot=bot,
        )


@router.callback_query(F.data.startswith("axa:"))
async def approve_callback(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or query.message is None:
        await query.answer()
        return
    chat = query.message.chat
    if not await is_admin(bot, chat.id, query.from_user.id):
        await query.answer("Admins only ✨", show_alert=True)
        return
    uid = int(parts[2])
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_approved WHERE bot_id=$1 AND chat_id=$2 AND user_id=$3",
            bid,
            chat.id,
            uid,
        )
    _cache.pop((bid, chat.id, uid), None)
    await query.answer("Unapproved ✅")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

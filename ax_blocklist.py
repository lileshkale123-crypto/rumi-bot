"""Blocklists: /addblocklist /rmblocklist /blocklist /unblocklistall /blocklistmode
/blocklistdelete /setblocklistreason /resetblocklistreason + outer middleware (aiogram 3.x, bot_id aware)."""
import logging
import re
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

import ax_actions
import ax_approve
import ax_lockcore
import ax_mod
import bot_context
from ax_common import cmd_args, esc, is_admin
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_blocklist")

MODES = ("nothing", "ban", "mute", "kick", "warn", "tban", "tmute")
_TAG = re.compile(r"\{([^}]+)\}")

_cache: dict[tuple[int, int], tuple[float, list[tuple[re.Pattern, str, str | None]], dict]] = {}
_CACHE_MAX = 5000
_CACHE_TTL = 30.0
_middleware_registered = False


def parse_triggers(body: str) -> tuple[list[str], str]:
    body = body.strip()
    if body.startswith("("):
        end = body.find(")")
        if end == -1:
            return [], body
        inner, rest = body[1:end], body[end + 1 :]
        trig = [t.strip().strip('"') for t in re.findall(r'"[^"]*"|[^,]+', inner)]
        return [t for t in trig if t], rest.strip()
    if body.startswith('"'):
        end = body.find('"', 1)
        if end == -1:
            return [], body
        return [body[1:end]], body[end + 1 :].strip()
    parts = body.split(None, 1)
    return ([parts[0]] if parts else []), (parts[1] if len(parts) > 1 else "")


def _glob(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s.startswith("**", i):
            out.append(".*")
            i += 2
        elif s[i] == "*":
            out.append(r"\S*")
            i += 1
        elif s[i] == "?":
            out.append(r"\S")
            i += 1
        else:
            out.append(re.escape(s[i]))
            i += 1
    return "".join(out)


def compile_trigger(trig: str) -> re.Pattern:
    t = trig.strip()
    low = t.lower()
    if low.startswith("exact:"):
        return re.compile(r"^\s*" + _glob(t[6:]) + r"\s*$", re.I)
    if low.startswith("prefix:"):
        return re.compile(r"^\s*" + _glob(t[7:]), re.I)
    return re.compile(r"(?<!\w)" + _glob(t) + r"(?!\w)", re.I)


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX:
        return
    expired = [k for k, (ts, _, _) in _cache.items() if now - ts >= _CACHE_TTL]
    for k in expired:
        _cache.pop(k, None)
    if len(_cache) > _CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for k, _ in oldest:
            _cache.pop(k, None)


async def load(pool: asyncpg.Pool, chat_id: int, bot_id: int | None = None):
    bid = bot_context.resolve_bot_id(bot_id)
    key = (bid, chat_id)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1], hit[2]
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT trigger, reason FROM ax_blocklist WHERE bot_id=$1 AND chat_id=$2",
            bid,
            chat_id,
        )
        s = await c.fetchrow(
            "SELECT * FROM ax_blocklist_settings WHERE bot_id=$1 AND chat_id=$2",
            bid,
            chat_id,
        )
    settings = (
        dict(s)
        if s
        else {"mode": "nothing", "mode_secs": 3600, "delete_msg": True, "default_reason": None}
    )
    comp = []
    for r in rows:
        try:
            comp.append((compile_trigger(r["trigger"]), r["trigger"], r["reason"]))
        except re.error:
            pass
    _prune_cache(now)
    _cache[key] = (now, comp, settings)
    return comp, settings


def invalidate(chat_id: int, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    _cache.pop((bid, chat_id), None)


async def check_blocklist(msg: Message, bot: Bot, pool: asyncpg.Pool) -> bool:
    """Returns True if message matched a blocklist trigger and was deleted (stop further handling)."""
    chat = msg.chat
    user = msg.from_user
    if chat.type == "private" or user is None or user.is_bot:
        return False
    if msg.text and msg.text.startswith("/"):
        return False
    text = msg.text or msg.caption
    if not text:
        return False

    bid = bot.id
    comp, s = await load(pool, chat.id, bot_id=bid)
    if not comp:
        return False

    hit = next(((p, t, r) for p, t, r in comp if p.search(text)), None)
    if hit is None:
        return False

    if await ax_approve.is_approved(pool, chat.id, user.id, bot_id=bid) or await ax_lockcore._is_admin_cached(
        bot, chat.id, user.id
    ):
        return False

    _, trig, raw_reason = hit
    tags = [m.strip() for m in _TAG.findall(raw_reason or "")]
    reason = _TAG.sub("", raw_reason or "").strip() or s["default_reason"]
    delete = s["delete_msg"]
    action = (
        s["mode"]
        if s["mode"] == "nothing"
        else (s["mode"] + (f" {s['mode_secs']}s" if s["mode"] in ("tban", "tmute") else ""))
    )
    for t in tags:
        low = t.lower()
        if low == "nodel":
            delete = False
        elif low == "del":
            delete = True
        elif ax_actions.parse_action(t)[0] is not None:
            action = t

    if delete:
        try:
            await msg.delete()
        except Exception:
            pass

    if action and action != "nothing":
        try:
            await ax_actions.run(
                bot, pool, chat.id, user, action, reason or f"Blocklisted: {trig}", bot_id=bid
            )
        except Exception as e:
            log.warning("blocklist action failed: %s", e)

    return bool(delete)


class BlocklistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                if await check_blocklist(event, bot, pool):
                    return None
        return await handler(event, data)


async def _gate(message: Message, bot: Bot) -> bool:
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
    if not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
            bot=bot,
        )
        return False
    return True


@router.message(Command("addblocklist"))
async def add_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    text = message.text or ""
    parts = text.split(None, 1)
    body = parts[1] if len(parts) > 1 else ""
    trigs, reason = parse_triggers(body)
    if not trigs:
        await ax_mod.say(
            message,
            '🚫 <b>Add what?</b>\n<blockquote>/addblocklist word reason\n'
            '/addblocklist "a sentence" reason\n/addblocklist (hi, hey) reason\n'
            'Wildcards: ? * **\nActions: {tmute 6h} {warn} {nodel}</blockquote>',
            bot=bot,
        )
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        for t in trigs:
            await c.execute(
                "INSERT INTO ax_blocklist (bot_id, chat_id, trigger, reason) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (chat_id, trigger) DO UPDATE SET bot_id=$1, reason=$4",
                bid,
                chat_id,
                t.lower(),
                reason or None,
            )
    invalidate(chat_id, bot_id=bid)
    await ax_mod.say(
        message,
        f"🚫 <b>Blocklisted</b>\n<blockquote>{esc(', '.join(trigs))}</blockquote>",
        bot=bot,
    )


@router.message(Command("rmblocklist"))
async def rm_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    text = message.text or ""
    parts = text.split(None, 1)
    body = parts[1] if len(parts) > 1 else ""
    trigs, _ = parse_triggers(body)
    if not trigs:
        await ax_mod.say(
            message,
            "💫 <b>Remove what?</b>\n<blockquote>Example: /rmblocklist word</blockquote>",
            bot=bot,
        )
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_blocklist WHERE bot_id=$1 AND chat_id=$2 AND trigger = ANY($3::text[])",
            bid,
            chat_id,
            [t.lower() for t in trigs],
        )
    invalidate(chat_id, bot_id=bid)
    await ax_mod.say(
        message,
        f"✅ <b>Removed</b>\n<blockquote>{esc(', '.join(trigs))}</blockquote>",
        bot=bot,
    )


@router.message(Command("blocklist"))
async def list_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT trigger FROM ax_blocklist WHERE bot_id=$1 AND chat_id=$2 ORDER BY trigger LIMIT 100",
            bid,
            chat_id,
        )
    if not rows:
        await ax_mod.say(message, "🚫 <b>Blocklist</b>\n<blockquote>Empty</blockquote>", bot=bot)
        return
    await ax_mod.say(
        message,
        "🚫 <b>Blocklist</b>\n<blockquote>"
        + "\n".join(f"• {esc(r['trigger'])}" for r in rows)
        + "</blockquote>",
        bot=bot,
    )


@router.message(Command("unblocklistall"))
async def clearall_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    chat = message.chat
    user = message.from_user
    if chat.type == "private" or user is None:
        return
    try:
        m = await bot.get_chat_member(chat.id, user.id)
    except Exception:
        return
    if m.status != ChatMemberStatus.CREATOR:
        await ax_mod.say(
            message,
            "👑 <b>Owner only</b>\n<blockquote>Only the group creator can clear the whole blocklist</blockquote>",
            bot=bot,
        )
        return
    bid = bot_ctx.bot_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_blocklist WHERE bot_id=$1 AND chat_id=$2", bid, chat.id)
    invalidate(chat.id, bot_id=bid)
    await ax_mod.say(
        message,
        "✅ <b>Blocklist cleared</b>\n<blockquote>All triggers removed</blockquote>",
        bot=bot,
    )


async def _set(pool: asyncpg.Pool, chat_id: int, col: str, val, bot_id: int) -> None:
    assert col in ("mode", "mode_secs", "delete_msg", "default_reason")
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_blocklist_settings (bot_id, chat_id, {col}) VALUES ($1,$2,$3) "
            f"ON CONFLICT (chat_id) DO UPDATE SET bot_id=$1, {col}=$3",
            bot_id,
            chat_id,
            val,
        )
    invalidate(chat_id, bot_id=bot_id)


@router.message(Command("blocklistmode"))
async def mode_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    args = [a.lower() for a in cmd_args(message)]
    if not args or args[0] not in MODES:
        _, s = await load(pool, chat_id, bot_id=bid)
        await ax_mod.say(
            message,
            f"⚙️ <b>Blocklist mode</b>\n<blockquote>Currently <b>{s['mode']}</b>\n"
            "Set: /blocklistmode nothing/ban/mute/kick/warn/tban/tmute</blockquote>",
            bot=bot,
        )
        return
    if args[0] in ("tban", "tmute") and len(args) > 1:
        secs = ax_actions.parse_dur(args[1])
        if not secs:
            await ax_mod.say(
                message,
                "⏳ <b>Bad time</b>\n<blockquote>Example: /blocklistmode tmute 6h</blockquote>",
                bot=bot,
            )
            return
        await _set(pool, chat_id, "mode_secs", secs, bid)
    await _set(pool, chat_id, "mode", args[0], bid)
    await ax_mod.say(
        message,
        f"✅ <b>Mode set</b>\n<blockquote>Blocklisted words → <b>{args[0]}</b></blockquote>",
        bot=bot,
    )


@router.message(Command("blocklistdelete"))
async def delete_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    bid = bot_ctx.bot_id
    args = cmd_args(message)
    arg = args[0].lower() if args else ""
    if arg not in ("on", "off", "yes", "no"):
        _, s = await load(pool, chat_id, bot_id=bid)
        await ax_mod.say(
            message,
            f"⚙️ <b>Blocklist delete</b>\n<blockquote>Currently <b>{'on' if s['delete_msg'] else 'off'}</b>\n"
            "Use /blocklistdelete on or off</blockquote>",
            bot=bot,
        )
        return
    val = arg in ("on", "yes")
    await _set(pool, chat_id, "delete_msg", val, bid)
    await ax_mod.say(
        message,
        f"✅ <b>Delete {'on' if val else 'off'}</b>\n<blockquote>Blocklisted messages are {'deleted' if val else 'kept'}</blockquote>",
        bot=bot,
    )


@router.message(Command("setblocklistreason"))
async def setreason_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    text = message.text or ""
    parts = text.split(None, 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    if not body:
        await ax_mod.say(
            message,
            "💫 <b>Reason?</b>\n<blockquote>Example: /setblocklistreason Please follow the rules</blockquote>",
            bot=bot,
        )
        return
    await _set(pool, message.chat.id, "default_reason", body, bot_ctx.bot_id)
    await ax_mod.say(
        message,
        "✅ <b>Default reason set</b>\n<blockquote>" + esc(body) + "</blockquote>",
        bot=bot,
    )


@router.message(Command("resetblocklistreason"))
async def resetreason_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not await _gate(message, bot):
        return
    await _set(pool, message.chat.id, "default_reason", None, bot_ctx.bot_id)
    await ax_mod.say(
        message,
        "✅ <b>Default reason cleared</b>\n<blockquote>Back to no reason</blockquote>",
        bot=bot,
    )


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(BlocklistMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

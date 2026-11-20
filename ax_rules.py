"""Rules: /rules /setrules /privaterules /resetrules /setrulesbutton /resetrulesbutton (aiogram 3.x)."""
import re
import time

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import ax_mod
from ax_common import cmd_args, esc, is_admin

router = Router(name="ax_rules")

DEFAULT = "No rules have been set yet. Admins can add them with /setrules"
COLS = ("rules_text", "private", "button_text")
_DM_RULES_RE = re.compile(r"^/start(?:@\w+)?\s+rules_(-?\d+)$")

_cache: dict[int, tuple[float, dict]] = {}
_CACHE_TTL = 60.0
_CACHE_MAX = 5000


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX:
        return
    expired = [cid for cid, (ts, _) in _cache.items() if now - ts >= _CACHE_TTL]
    for cid in expired:
        _cache.pop(cid, None)
    if len(_cache) > _CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for cid, _ in oldest:
            _cache.pop(cid, None)


def invalidate(chat_id: int) -> None:
    _cache.pop(chat_id, None)


async def get(pool: asyncpg.Pool, chat_id: int) -> dict:
    now = time.time()
    hit = _cache.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return dict(hit[1])
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM ax_rules WHERE chat_id=$1", chat_id)
    val = dict(r) if r else {"rules_text": None, "private": False, "button_text": None}
    _prune_cache(now)
    _cache[chat_id] = (now, val)
    return dict(val)


async def _set(pool: asyncpg.Pool, chat_id: int, col: str, val) -> None:
    assert col in COLS
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_rules (chat_id, {col}) VALUES ($1,$2) "
            f"ON CONFLICT (chat_id) DO UPDATE SET {col}=$2",
            chat_id,
            val,
        )
    invalidate(chat_id)


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


def _body(message: Message) -> str:
    parts = (message.text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


@router.message(F.chat.type == "private", F.text.regexp(r"^/start(?:@\w+)?\s+rules_(-?\d+)$"))
async def dm_rules(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    m = _DM_RULES_RE.match(message.text or "")
    if not m or user is None:
        return
    cid = int(m.group(1))
    r = await get(pool, cid)
    title = "this group"
    try:
        title = (await bot.get_chat(cid)).title or title
    except Exception:
        pass
    text = r["rules_text"] or DEFAULT
    await message.reply(
        f"📜 <b>Rules of {esc(title)}</b>\n<blockquote>{esc(text)}</blockquote>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("rules"))
async def rules_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use /rules inside a group 💫</blockquote>",
            bot=bot,
        )
        return
    r = await get(pool, chat.id)
    text = r["rules_text"] or DEFAULT
    args = cmd_args(message)
    if args and args[0].lower() == "noformat":
        await message.reply(text)
        return
    card = f"📜 <b>Rules of {esc(chat.title or 'this group')}</b>\n<blockquote>{esc(text)}</blockquote>"
    if r["private"]:
        try:
            await bot.send_message(user.id, card, parse_mode=ParseMode.HTML)
            await ax_mod.say(
                message,
                "📩 <b>Rules sent</b>\n<blockquote>Check your private chat with me 💜</blockquote>",
                bot=bot,
            )
        except Exception:
            await ax_mod.say(
                message,
                "💫 <b>Open a chat with me first</b>\n<blockquote>Send me /start in private, then try /rules again</blockquote>",
                bot=bot,
            )
        return
    await ax_mod.say(message, card, bot=bot)


@router.message(Command("setrules"))
async def setrules_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    body = _body(message)
    if not body and message.reply_to_message:
        body = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not body:
        await ax_mod.say(
            message,
            "💫 <b>Rules?</b>\n<blockquote>Example: /setrules 1. Be kind\n2. No spam</blockquote>",
            bot=bot,
        )
        return
    await _set(pool, message.chat.id, "rules_text", body)
    await ax_mod.say(
        message,
        "✅ <b>Rules saved</b>\n<blockquote>Members can read them with /rules 📜</blockquote>",
        bot=bot,
    )


@router.message(Command("resetrules"))
async def resetrules_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    await _set(pool, message.chat.id, "rules_text", None)
    await ax_mod.say(
        message,
        "✅ <b>Rules cleared</b>\n<blockquote>Back to the default message</blockquote>",
        bot=bot,
    )


@router.message(Command("privaterules"))
async def privaterules_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = cmd_args(message)
    arg = args[0].lower() if args else ""
    if arg not in ("on", "off", "yes", "no"):
        r = await get(pool, chat_id)
        await ax_mod.say(
            message,
            f"⚙️ <b>Private rules</b>\n<blockquote>Currently <b>{'on' if r['private'] else 'off'}</b>\n"
            "Use /privaterules on or off</blockquote>",
            bot=bot,
        )
        return
    val = arg in ("on", "yes")
    await _set(pool, chat_id, "private", val)
    await ax_mod.say(
        message,
        f"✅ <b>Private rules {'on' if val else 'off'}</b>\n<blockquote>"
        f"/rules now goes {'to their PM' if val else 'in the group'}</blockquote>",
        bot=bot,
    )


@router.message(Command("setrulesbutton"))
async def setbutton_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    body = _body(message)
    if not body:
        await ax_mod.say(
            message,
            "💫 <b>Button text?</b>\n<blockquote>Example: /setrulesbutton Read the rules</blockquote>",
            bot=bot,
        )
        return
    await _set(pool, message.chat.id, "button_text", body[:40])
    await ax_mod.say(
        message,
        "✅ <b>Rules button renamed</b>\n<blockquote>Used by the {rules} tag in welcomes 💜</blockquote>",
        bot=bot,
    )


@router.message(Command("resetrulesbutton"))
async def resetbutton_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    await _set(pool, message.chat.id, "button_text", None)
    await ax_mod.say(
        message,
        "✅ <b>Button name reset</b>\n<blockquote>Back to the default</blockquote>",
        bot=bot,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

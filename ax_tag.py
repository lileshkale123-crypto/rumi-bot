"""/tagall (admins only) and /admins (anyone) (aiogram 3.x)."""
import asyncio
import html
import logging
import time

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message

from ax_common import cmd_args, fire, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_tag")

MAX_TAG = 200
BATCH = 5
TAGALL_COOLDOWN = 300
ADMINS_COOLDOWN = 30
_last: dict[tuple[str, int], float] = {}
_LAST_MAX = 5000


def _mention(uid: int, name: str | None) -> str:
    return f'<a href="tg://user?id={uid}">{html.escape(name or "friend")}</a>'


def _left(key: tuple[str, int], secs: int) -> float:
    return secs - (time.monotonic() - _last.get(key, -1e9))


def _prune_last(now: float) -> None:
    if len(_last) <= _LAST_MAX:
        return
    stale = [k for k, ts_val in _last.items() if now - ts_val >= TAGALL_COOLDOWN * 2]
    for k in stale:
        _last.pop(k, None)


async def _send_batches(bot: Bot, chat_id: int, header: str, people: list[tuple[int, str | None]]) -> None:
    for i in range(0, len(people), BATCH):
        line = " ".join(_mention(u, n) for u, n in people[i : i + BATCH])
        text = f"{header}\n\n{line}" if i == 0 else line
        for attempt in range(2):
            try:
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                break
            except TelegramRetryAfter as exc:
                if attempt == 1:
                    break
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except TelegramForbiddenError:
                return
            except Exception:
                log.exception("tag batch failed")
                break
        await asyncio.sleep(1.5)


@router.message(Command("tagall"))
async def tagall_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if chat.type not in ("group", "supergroup"):
        await message.reply("⚠️ This command works only in groups.", parse_mode=ParseMode.HTML)
        return
    anon = bool(message.sender_chat and message.sender_chat.id == chat.id)
    if not anon and (user is None or not await is_admin(bot, chat.id, user.id)):
        await message.reply("🔒 <b>Only admins</b> can use /tagall.", parse_mode=ParseMode.HTML)
        return
    left = _left(("all", chat.id), TAGALL_COOLDOWN)
    if left > 0:
        await message.reply(
            f"⏳ <b>Easy there!</b> Try again in {int(left) + 1}s.",
            parse_mode=ParseMode.HTML,
        )
        return

    now = time.monotonic()
    _prune_last(now)
    _last[("all", chat.id)] = now

    people: dict[int, str | None] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT u.id, u.first_name FROM ax_chat_members m "
            "JOIN users u ON u.id = m.user_id WHERE m.chat_id = $1 LIMIT 500",
            chat.id,
        )
    for r in rows:
        people[r["id"]] = r["first_name"]
    try:
        for a in await bot.get_chat_administrators(chat.id):
            if not a.user.is_bot:
                people[a.user.id] = a.user.first_name
    except Exception:
        log.exception("get admins failed")

    items = list(people.items())[:MAX_TAG]
    if not items:
        await message.reply("😿 I don't know anyone here yet.", parse_mode=ParseMode.HTML)
        return
    note = html.escape(" ".join(cmd_args(message))[:200]) or "Everyone, please check in! 🌙"
    header = f"📣 <b>ATTENTION</b>\n<blockquote>{note}</blockquote>"
    fire(_send_batches(bot, chat.id, header, items))


@router.message(Command("admins"))
async def admins_cmd(message: Message, bot: Bot) -> None:
    chat = message.chat
    if chat.type not in ("group", "supergroup"):
        await message.reply("⚠️ This command works only in groups.", parse_mode=ParseMode.HTML)
        return
    left = _left(("adm", chat.id), ADMINS_COOLDOWN)
    if left > 0:
        await message.reply(
            f"⏳ Admins were just called. Wait {int(left) + 1}s.",
            parse_mode=ParseMode.HTML,
        )
        return

    now = time.monotonic()
    _prune_last(now)
    _last[("adm", chat.id)] = now

    try:
        admins = [
            (a.user.id, a.user.first_name)
            for a in await bot.get_chat_administrators(chat.id)
            if not a.user.is_bot
        ]
    except Exception:
        await message.reply("😿 I couldn't fetch the admins right now.", parse_mode=ParseMode.HTML)
        return

    if not admins:
        await message.reply("😿 No human admins found here.", parse_mode=ParseMode.HTML)
        return

    note = html.escape(" ".join(cmd_args(message))[:200]) or "Someone needs help!"
    line = " ".join(_mention(u, n) for u, n in admins)
    await message.reply(
        f"🚨 <b>CALLING ADMINS</b>\n<blockquote>{note}</blockquote>\n\n{line}",
        parse_mode=ParseMode.HTML,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

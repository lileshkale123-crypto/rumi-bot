"""Captcha for new joiners: mute until solved, kick on timeout (aiogram 3.x + APScheduler)."""
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, ChatPermissions, InlineKeyboardMarkup, Message, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import ax_approve
import ax_rules
from ax_common import btn, esc, fire, get_bot_username

log = logging.getLogger(__name__)
router = Router(name="ax_captcha")

SOLVED = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)
MUTED = ChatPermissions(can_send_messages=False)

_SC: dict[int, tuple[float, dict]] = {}
_SC_MAX = 5000
_SC_TTL = 20.0

# Fast in-memory index of (chat_id, user_id) awaiting text/math captcha answers
_pending_answers: set[tuple[int, int]] = set()
_pending_loaded = False
_middleware_registered = False

NEED = [True]


def _prune_sc(now: float) -> None:
    if len(_SC) <= _SC_MAX:
        return
    expired = [cid for cid, (ts, _) in _SC.items() if now - ts >= _SC_TTL * 5]
    for cid in expired:
        _SC.pop(cid, None)
    if len(_SC) > _SC_MAX:
        oldest = sorted(_SC.items(), key=lambda kv: kv[1][0])[: _SC_MAX // 5]
        for cid, _ in oldest:
            _SC.pop(cid, None)


async def _reload(pool: asyncpg.Pool, chat_id: int) -> dict:
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM ax_captcha_settings WHERE chat_id=$1", chat_id)
    v = (
        dict(r)
        if r
        else {
            "enabled": False,
            "mode": "button",
            "mute_secs": 0,
            "kick": True,
            "kick_secs": 300,
            "rules": False,
            "btn_text": None,
        }
    )
    now = time.time()
    _prune_sc(now)
    _SC[chat_id] = (now, v)
    return dict(v)


async def settings(pool: asyncpg.Pool, chat_id: int) -> dict:
    hit = _SC.get(chat_id)
    if hit:
        now = time.time()
        if now - hit[0] >= _SC_TTL:
            _SC[chat_id] = (now, hit[1])  # one refresh at a time
            fire(_reload(pool, chat_id))
        return dict(hit[1])
    return await _reload(pool, chat_id)


def challenge(mode: str) -> tuple[str | None, str | None]:
    if mode == "math":
        a, b = random.randint(2, 9), random.randint(2, 9)
        return f"{a} + {b} = ?", str(a + b)
    if mode == "text":
        w = "".join(random.choice("ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(4))
        return f"Type this code: <code>{w}</code>", w
    return None, None


async def join(msg: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = msg.chat
    if chat.type == "private" or not msg.new_chat_members:
        return
    s = await settings(pool, chat.id)
    if not s["enabled"]:
        return

    bot_username = await get_bot_username(bot)

    for m in msg.new_chat_members:
        if m.is_bot or m.id == bot.id:
            continue
        if await ax_approve.is_approved(pool, chat.id, m.id):
            continue
        try:
            await bot.restrict_chat_member(chat.id, m.id, permissions=MUTED)
        except Exception as e:
            log.warning("CAPTCHA mute failed for user %s in chat %s: %r", m.id, chat.id, e)
            continue  # continue checking remaining new members instead of aborting the batch
        try:
            q, ans = challenge(s["mode"])
            name = esc(m.first_name or "friend")
            rows = []
            if q:
                body = (
                    f"🔐 <b>Verify, {name}</b>\n<blockquote>{q}\n"
                    f"Send the answer here ✨</blockquote>"
                )
            else:
                body = (
                    f"🔐 <b>Welcome, {name}</b>\n<blockquote>Tap the "
                    f"button to prove you are human ✨</blockquote>"
                )
                label = s["btn_text"] or "✅ I am human"
                rows.append([btn(label, f"axc:{m.id}")])
            if s.get("rules") and bot_username:
                r_cfg = await ax_rules.get(pool, chat.id)
                r_label = r_cfg.get("button_text") or "📜 Rules"
                rows.append([btn(r_label, url=f"https://t.me/{bot_username}?start=rules_{chat.id}")])

            kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
            sent = await bot.send_message(
                chat.id,
                body,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            exp = datetime.now(timezone.utc) + timedelta(seconds=s["kick_secs"])
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ax_captcha_pending VALUES ($1,$2,$3,$4,$5) "
                    "ON CONFLICT (chat_id,user_id) DO UPDATE SET answer=$3, "
                    "msg_id=$4, expires=$5",
                    chat.id,
                    m.id,
                    ans,
                    sent.message_id,
                    exp,
                )
            if ans:
                _pending_answers.add((chat.id, m.id))
            NEED[0] = True
        except Exception:
            log.exception("CAPTCHA send failed")


async def finish(bot: Bot, pool: asyncpg.Pool, chat_id: int, user_id: int, s: dict) -> bool:
    _pending_answers.discard((chat_id, user_id))
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "DELETE FROM ax_captcha_pending WHERE chat_id=$1 AND "
            "user_id=$2 RETURNING msg_id",
            chat_id,
            user_id,
        )
    if not r:
        return False
    try:
        if s["mute_secs"] > 0:
            until = datetime.now(timezone.utc) + timedelta(seconds=s["mute_secs"])
            await bot.restrict_chat_member(chat_id, user_id, permissions=MUTED, until_date=until)
        else:
            await bot.restrict_chat_member(chat_id, user_id, permissions=SOLVED)
        if r["msg_id"]:
            await bot.delete_message(chat_id, r["msg_id"])
    except Exception:
        pass
    return True


@router.callback_query(F.data.regexp(r"^axc:\d+$"))
async def press(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if query.message is None or not query.data:
        await query.answer()
        return
    uid = int(query.data.split(":")[1])
    if query.from_user.id != uid:
        await query.answer("This button is not for you 🌸", show_alert=True)
        return
    s = await settings(pool, query.message.chat.id)
    ok = await finish(bot, pool, query.message.chat.id, uid, s)
    await query.answer("Verified, welcome ✨" if ok else "Already done")


async def _handle_answer(msg: Message, bot: Bot, pool: asyncpg.Pool) -> bool:
    """Returns True if message was a captcha attempt and consumed."""
    chat = msg.chat
    user = msg.from_user
    if chat.type == "private" or user is None or not msg.text or msg.text.startswith("/"):
        return False
    key = (chat.id, user.id)
    if _pending_loaded and key not in _pending_answers:
        return False

    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT answer FROM ax_captcha_pending WHERE chat_id=$1 AND user_id=$2",
            chat.id,
            user.id,
        )
    if not r or not r["answer"]:
        _pending_answers.discard(key)
        return False

    try:
        await msg.delete()
    except Exception:
        pass

    if msg.text.strip().upper() == r["answer"].upper():
        s = await settings(pool, chat.id)
        await finish(bot, pool, chat.id, user.id, s)
    return True


class CaptchaMiddleware(BaseMiddleware):
    """Handles new-member captcha setup (while letting welcome run) and consumes text/math answers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.type != "private":
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                if event.new_chat_members:
                    await join(event, bot, pool)
                elif event.text and not event.text.startswith("/"):
                    if await _handle_answer(event, bot, pool):
                        return None
        return await handler(event, data)


async def expire_job(bot: Bot, pool: asyncpg.Pool) -> None:
    if not NEED[0]:
        return
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "DELETE FROM ax_captcha_pending WHERE expires < NOW() "
                "RETURNING chat_id, user_id, msg_id"
            )
        for r in rows:
            _pending_answers.discard((r["chat_id"], r["user_id"]))
            s = await settings(pool, r["chat_id"])
            try:
                if r["msg_id"]:
                    await bot.delete_message(r["chat_id"], r["msg_id"])
                if s["kick"]:
                    await bot.ban_chat_member(r["chat_id"], r["user_id"])
                    await bot.unban_chat_member(r["chat_id"], r["user_id"])
            except Exception:
                pass
        async with pool.acquire() as c:
            if not await c.fetchval("SELECT count(*) FROM ax_captcha_pending"):
                NEED[0] = False
                _pending_answers.clear()
    except Exception:
        log.exception("captcha expire_job failed")


async def on_startup(bot: Bot, dispatcher) -> None:
    global _pending_loaded
    pool: asyncpg.Pool = dispatcher["pool"]
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]

    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT chat_id, user_id FROM ax_captcha_pending WHERE answer IS NOT NULL"
            )
        _pending_answers.clear()
        _pending_answers.update((r["chat_id"], r["user_id"]) for r in rows)
        _pending_loaded = True
    except Exception:
        log.exception("Failed loading pending captcha answers")

    scheduler.add_job(
        expire_job,
        trigger="interval",
        seconds=60,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=20),
        args=[bot, pool],
        id="ax_captcha:expire_job",
        replace_existing=True,
    )


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(CaptchaMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

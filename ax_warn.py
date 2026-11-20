"""Warn system with reasons, per-group limit, mode and expiry (aiogram 3.x).
/warn /dwarn /swarn /warns /rmwarn /unwarn (+ Remove-warn button)."""
import logging
import time as _time
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import ax_mod
from ax_common import btn, cmd_args, esc, fire, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_warn")

ICONS = {"ban": "🔨", "mute": "🔇", "kick": "👢", "tban": "🔨", "tmute": "🔇"}

_SCACHE: dict[int, tuple[float, dict]] = {}
_SCACHE_MAX = 5000
_SCACHE_TTL = 60.0


def _prune_scache(now: float) -> None:
    if len(_SCACHE) <= _SCACHE_MAX:
        return
    expired = [cid for cid, (ts, _) in _SCACHE.items() if now - ts >= _SCACHE_TTL]
    for cid in expired:
        _SCACHE.pop(cid, None)
    if len(_SCACHE) > _SCACHE_MAX:
        oldest = sorted(_SCACHE.items(), key=lambda kv: kv[1][0])[: _SCACHE_MAX // 5]
        for cid, _ in oldest:
            _SCACHE.pop(cid, None)


async def get_settings(c, chat_id: int) -> dict:
    now = _time.time()
    hit = _SCACHE.get(chat_id)
    if hit and now - hit[0] < _SCACHE_TTL:
        return dict(hit[1])
    row = await c.fetchrow("SELECT * FROM ax_warn_settings WHERE chat_id=$1", chat_id)
    s = (
        dict(row)
        if row
        else {"warn_limit": 3, "warn_mode": "mute", "warn_mode_secs": 86400, "warn_expire_secs": 0}
    )
    _prune_scache(now)
    _SCACHE[chat_id] = (now, s)
    return dict(s)


async def add_warn(pool: asyncpg.Pool, chat_id: int, uid: int, reason: str | None) -> tuple[dict, int]:
    """Save a warning and count the active ones in a single database round trip."""
    async with pool.acquire() as c:
        s = await get_settings(c, chat_id)
        n = await c.fetchval(
            "WITH ins AS (INSERT INTO ax_warn_log (chat_id, user_id, reason) VALUES ($1,$2,$3) RETURNING 1) "
            "SELECT 1 + (SELECT COUNT(*) FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2 "
            "AND ($4::float8 <= 0 OR created_at > now() - make_interval(secs => $4::float8)))",
            chat_id,
            uid,
            reason,
            float(s["warn_expire_secs"]),
        )
    return s, int(n)


async def count_active(c, chat_id: int, uid: int, expire_secs: int) -> int:
    if expire_secs > 0:
        return int(
            await c.fetchval(
                "SELECT COUNT(*) FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2 "
                "AND created_at > now() - make_interval(secs => $3)",
                chat_id,
                uid,
                float(expire_secs),
            )
        )
    return int(
        await c.fetchval(
            "SELECT COUNT(*) FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2",
            chat_id,
            uid,
        )
    )


async def remove_latest(c, chat_id: int, uid: int):
    return await c.fetchval(
        "DELETE FROM ax_warn_log WHERE id = (SELECT id FROM ax_warn_log "
        "WHERE chat_id=$1 AND user_id=$2 ORDER BY id DESC LIMIT 1) RETURNING id",
        chat_id,
        uid,
    )


async def punish(bot: Bot, pool: asyncpg.Pool, chat_id: int, uid: int, mode: str, secs: int) -> None:
    if mode in ("tban", "tmute"):
        until = datetime.now(timezone.utc) + timedelta(seconds=secs)
        kind = "ban" if mode == "tban" else "mute"
        if kind == "mute":
            await bot.restrict_chat_member(chat_id, uid, permissions=ax_mod.MUTED, until_date=until)
        else:
            await bot.ban_chat_member(chat_id, uid, until_date=until)
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_temp_punish (chat_id, user_id, ptype, expires_at, reason) "
                "VALUES ($1,$2,$3,$4,'warn limit') ON CONFLICT (chat_id, user_id, ptype) "
                "DO UPDATE SET expires_at=$4",
                chat_id,
                uid,
                kind,
                until,
            )
        ax_mod.NEED[0] = True
    else:
        await ax_mod.perform(mode, bot, pool, chat_id, uid)


async def _log_warn(bot: Bot, pool: asyncpg.Pool, chat_id: int, actor, target, detail: str) -> None:
    try:
        import ax_log

        actor_name = esc(getattr(actor, "first_name", None) or "Admin")
        await ax_log.send_log(
            bot,
            pool,
            chat_id,
            "warn",
            f"<b>warn</b>: {esc(target.first_name or 'User')} (<code>{target.id}</code>){detail}\n"
            f"By: {actor_name}",
        )
    except Exception:
        pass


def make_warn(mode: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        if mode == "d" and not message.reply_to_message:
            await ax_mod.say(
                message,
                "💫 <b>Reply needed</b>\n<blockquote>Reply to the message you want removed</blockquote>",
                bot=bot,
            )
            return

        target = await ax_mod.gate(message, bot, pool)
        if target is None:
            return

        args = cmd_args(message)
        d = args if message.reply_to_message else args[1:]
        reason = " ".join(d) or None
        chat_id = message.chat.id

        s, n = await add_warn(pool, chat_id, target.id, reason)
        limit = s["warn_limit"]
        note = f"\n📝 {esc(reason)}" if reason else ""

        if n >= limit:
            try:
                await punish(bot, pool, chat_id, target.id, s["warn_mode"], s["warn_mode_secs"])
            except Exception as e:
                await ax_mod.say(
                    message,
                    f"⚠️ <b>Couldn't punish</b>\n<blockquote>{esc(str(e))}</blockquote>",
                    bot=bot,
                )
                return
            async with pool.acquire() as c:
                await c.execute(
                    "DELETE FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2",
                    chat_id,
                    target.id,
                )
            detail = f"\n📊 <b>{n}/{limit}</b> → <b>{s['warn_mode']}</b>{note}"
            text = ax_mod.card(
                ICONS.get(s["warn_mode"], "⚠️"),
                "Warn limit reached",
                target.first_name or "User",
                detail,
            )
            kb = None
        else:
            detail = f"\n📊 Warnings: <b>{n}/{limit}</b>{note}"
            text = ax_mod.card("⚠️", "Warned", target.first_name or "User", detail)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[btn("➖ Remove warn", f"axw:rm:{target.id}", style="success")]]
            )

        fire(_log_warn(bot, pool, chat_id, message.from_user, target, detail))

        if mode == "d":
            for m in (message.reply_to_message, message):
                if m is not None:
                    try:
                        await m.delete()
                    except Exception:
                        pass
        elif mode == "s":
            try:
                await message.delete()
            except Exception:
                pass
            return

        await ax_mod.say(message, text, kb, bot=bot)

    return cmd


@router.message(Command("warns"))
async def warns_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    target = await ax_mod.gate(message, bot, pool, False)
    if target is None:
        return
    chat_id = message.chat.id
    async with pool.acquire() as c:
        s = await get_settings(c, chat_id)
        rows = await c.fetch(
            "SELECT reason, created_at FROM ax_warn_log WHERE chat_id=$1 AND user_id=$2 "
            "ORDER BY id DESC LIMIT 10",
            chat_id,
            target.id,
        )
        n = await count_active(c, chat_id, target.id, s["warn_expire_secs"])
    if not rows:
        await ax_mod.say(
            message,
            ax_mod.card("✅", "Clean record", target.first_name or "User", "\n📊 No warnings"),
            bot=bot,
        )
        return
    lines = "\n".join(f"{i}. {esc(r['reason'] or 'No reason')}" for i, r in enumerate(rows, 1))
    await ax_mod.say(
        message,
        ax_mod.card(
            "📋",
            "Warnings",
            target.first_name or "User",
            f"\n📊 <b>{n}/{s['warn_limit']}</b> active\n{lines}",
        ),
        bot=bot,
    )


@router.message(Command("rmwarn", "unwarn"))
async def rmwarn_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    target = await ax_mod.gate(message, bot, pool, False)
    if target is None:
        return
    chat_id = message.chat.id
    async with pool.acquire() as c:
        done = await remove_latest(c, chat_id, target.id)
        s = await get_settings(c, chat_id)
        n = await count_active(c, chat_id, target.id, s["warn_expire_secs"])
    if not done:
        await ax_mod.say(
            message,
            ax_mod.card("✅", "Nothing to remove", target.first_name or "User", "\n📊 No warnings"),
            bot=bot,
        )
        return
    await ax_mod.say(
        message,
        ax_mod.card(
            "✅",
            "Warning removed",
            target.first_name or "User",
            f"\n📊 Warnings: <b>{n}/{s['warn_limit']}</b>",
        ),
        bot=bot,
    )


@router.callback_query(F.data.startswith("axw:"))
async def warn_callback(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or query.message is None:
        await query.answer()
        return
    chat = query.message.chat
    if not await is_admin(bot, chat.id, query.from_user.id, "can_restrict_members"):
        await query.answer("Admins only ✨", show_alert=True)
        return
    uid = int(parts[2])
    async with pool.acquire() as c:
        done = await remove_latest(c, chat.id, uid)
    await query.answer("Warning removed ✅" if done else "No warnings left")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


router.message(Command("warn"))(make_warn("n"))
router.message(Command("dwarn"))(make_warn("d"))
router.message(Command("swarn"))(make_warn("s"))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

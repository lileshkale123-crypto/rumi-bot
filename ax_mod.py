"""Moderation commands (aiogram 3.x + APScheduler, bot_id aware)."""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatPermissions, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import bot_context
from ax_common import (
    btn,
    cmd_args,
    esc,
    fire,
    get_member as ax_common_member,
    invalidate,
    is_admin,
    resolve_target,
    right_label,
)
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_mod")

MAX_WARNS = 3
FULL = ChatPermissions(
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
)
MUTED = ChatPermissions(can_send_messages=False)
META = {
    "mute": ("🔇", "Muted"),
    "unmute": ("🔊", "Unmuted"),
    "ban": ("🔨", "Banned"),
    "unban": ("✅", "Unbanned"),
    "kick": ("👢", "Kicked"),
}
UNDO = {"mute": ("unmute", "🔊 Unmute"), "ban": ("unban", "✅ Unban")}

DUR = re.compile(r"^(\d+)([smhdw])$")
_DUR_MULT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

PROMOTE_GRANTS = (
    "can_restrict_members",
    "can_delete_messages",
    "can_pin_messages",
    "can_invite_users",
)

NEED: dict[int, bool] = {}


def card(icon: str, title: str, name: str, extra: str = "") -> str:
    return f"{icon} <b>{title}</b>\n<blockquote>👤 <b>{esc(name)}</b>{extra}</blockquote>"


def parse_dur(t: str) -> int | None:
    m = DUR.match((t or "").strip().lower())
    if not m:
        return None
    return int(m[1]) * _DUR_MULT[m[2]]


async def say(
    message: Message,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
    bot: Bot | None = None,
):
    b = bot or message.bot
    bid = bot_context.resolve_bot_id(getattr(b, "id", None))
    try:
        import ax_misc3

        if "Admins only" in text and (
            (bid, message.chat.id) in ax_misc3.QUIET or message.chat.id in ax_misc3.QUIET
        ):
            return None
    except Exception:
        pass

    try:
        import ax_actiontopic

        tid = ax_actiontopic.MAP.get((bid, message.chat.id))
        if tid is None:
            tid = ax_actiontopic.MAP.get(message.chat.id)
    except Exception:
        tid = None

    if tid and b is not None and not hasattr(message, "_orig_msg"):
        try:
            return await b.send_message(
                message.chat.id,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                message_thread_id=tid,
            )
        except Exception:
            pass

    return await message.reply(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def act_and_reply(
    message: Message,
    action,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
    fail_title: str = "Couldn't do that",
    note: str = "",
    bot: Bot | None = None,
) -> bool:
    reply_task = asyncio.create_task(say(message, text, kb, bot=bot))
    try:
        await action
    except Exception as e:
        err = f"⚠️ <b>{fail_title}</b>\n<blockquote>{esc(e)}</blockquote>{note}"
        sent = None
        try:
            sent = await reply_task
        except Exception:
            pass
        if sent is not None and hasattr(sent, "edit_text"):
            try:
                await sent.edit_text(err, parse_mode=ParseMode.HTML)
                return False
            except Exception:
                pass
        await say(message, err, bot=bot)
        return False
    try:
        await reply_task
    except Exception:
        pass
    return True


async def gate(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    protect: bool = True,
    right: str | None = "can_restrict_members",
):
    """Check group, admin rights, and target. Returns target user or None."""
    chat = message.chat
    user = message.from_user
    if user is None:
        return None
    if chat.type == "private":
        await say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>",
            bot=bot,
        )
        return None

    rp = (
        message.reply_to_message.from_user
        if (message.reply_to_message and message.reply_to_message.from_user)
        else None
    )
    jobs = [
        is_admin(bot, chat.id, user.id, right),
        is_admin(bot, chat.id, bot.id, right),
        resolve_target(message, bot, pool, bot_id=bot.id),
    ]
    if protect and rp is not None:
        jobs.append(is_admin(bot, chat.id, rp.id))

    res = await asyncio.gather(*jobs)
    user_ok, bot_ok, target = res[0], res[1], res[2]

    if not user_ok:
        if right and await is_admin(bot, chat.id, user.id):
            await say(
                message,
                f"🛡️ <b>Admins only</b>\n<blockquote>You need the <b>{right_label(right)}</b> permission for this ✨</blockquote>",
                bot=bot,
            )
        else:
            await say(
                message,
                "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
                bot=bot,
            )
        return None

    if not bot_ok:
        need = right_label(right) if right else "admin"
        await say(
            message,
            f"⚠️ <b>I need admin rights</b>\n<blockquote>Promote me with the <b>{need}</b> permission 💫</blockquote>",
            bot=bot,
        )
        return None

    if target is None:
        await say(
            message,
            "💫 <b>Who?</b>\n<blockquote>Reply to someone, or use @username / user ID</blockquote>",
            bot=bot,
        )
        return None

    if protect:
        target_admin = res[3] if len(res) > 3 else await is_admin(bot, chat.id, target.id)
        if target.id == bot.id or target_admin:
            await say(
                message,
                "🛡️ <b>Protected</b>\n<blockquote>I can't act on admins ✨</blockquote>",
                bot=bot,
            )
            return None

    return target


async def clear_timer(
    pool: asyncpg.Pool,
    chat_id: int,
    uid: int,
    ptype: str,
    bot_id: int | None = None,
) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_temp_punish WHERE bot_id=$1 AND chat_id=$2 AND user_id=$3 AND ptype=$4",
            bid,
            chat_id,
            uid,
            ptype,
        )


async def perform(kind: str, bot: Bot, pool: asyncpg.Pool, chat_id: int, uid: int) -> None:
    bid = bot_context.resolve_bot_id(bot.id)
    if kind == "mute":
        await bot.restrict_chat_member(chat_id, uid, permissions=MUTED)
    elif kind == "unmute":
        await bot.restrict_chat_member(chat_id, uid, permissions=FULL)
        fire(clear_timer(pool, chat_id, uid, "mute", bot_id=bid))
    elif kind == "ban":
        await bot.ban_chat_member(chat_id, uid)
    elif kind == "unban":
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
        fire(clear_timer(pool, chat_id, uid, "ban", bot_id=bid))
    elif kind == "kick":
        await bot.ban_chat_member(chat_id, uid)
        await bot.unban_chat_member(chat_id, uid)


async def _log_action(bot: Bot, pool: asyncpg.Pool, chat_id: int, actor, kind: str, target, extra: str = "") -> None:
    try:
        import ax_log

        cat = {
            "mute": "mute",
            "tmute": "mute",
            "unmute": "mute",
            "ban": "ban",
            "tban": "ban",
            "unban": "ban",
            "kick": "kick",
        }.get(kind)
        if cat:
            actor_name = esc(getattr(actor, "first_name", None) or "Admin")
            await ax_log.send_log(
                bot,
                pool,
                chat_id,
                cat,
                f"<b>{kind}</b>: {esc(target.first_name or 'User')} "
                f"(<code>{target.id}</code>){extra}\n"
                f"By: {actor_name}",
            )
    except Exception:
        pass


def make_cmd(kind: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        target = await gate(message, bot, pool, kind in ("mute", "ban", "kick"))
        if target is None:
            return
        icon, title = META[kind]
        kb = None
        if kind in UNDO:
            k, label = UNDO[kind]
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[btn(label, f"axm:{k}:{target.id}", style="success")]]
            )
        ok = await act_and_reply(
            message,
            perform(kind, bot, pool, message.chat.id, target.id),
            card(icon, title, target.first_name or "User"),
            kb,
            bot=bot,
        )
        if ok:
            fire(_log_action(bot, pool, message.chat.id, message.from_user, kind, target))

    return cmd


@router.callback_query(F.data.startswith("axm:"))
async def mod_callback(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[1] not in META or not parts[2].isdigit() or query.message is None:
        await query.answer()
        return

    _, kind, uid_str = parts
    chat = query.message.chat
    if not await is_admin(bot, chat.id, query.from_user.id, "can_restrict_members"):
        await query.answer("You need the Ban users permission 🛡️", show_alert=True)
        return
    try:
        await perform(kind, bot, pool, chat.id, int(uid_str))
    except Exception as e:
        await query.answer(str(e)[:150], show_alert=True)
        return
    await query.answer("Done ✨")
    icon, title = META[kind]
    try:
        await query.message.edit_text(
            f"{icon} <b>{title}</b>\n<blockquote>By <b>{esc(query.from_user.first_name or 'Admin')}</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


@router.message(Command("promote"))
async def promote_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    target = await gate(message, bot, pool, False, "can_promote_members")
    if target is None or message.from_user is None:
        return
    chat_id = message.chat.id
    if target.id == bot.id:
        return

    args = cmd_args(message)
    parts = args if message.reply_to_message else args[1:]
    title = " ".join(parts)[:16] or None

    grant = {}
    for r in PROMOTE_GRANTS:
        if await is_admin(bot, chat_id, message.from_user.id, r):
            grant[r] = True
            break
    if not grant:
        await say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You can only give rights that you hold yourself ✨</blockquote>",
            bot=bot,
        )
        return
    if await is_admin(bot, chat_id, target.id):
        await say(
            message,
            "🛡️ <b>Already an admin</b>\n<blockquote>Their rights stay as they are ✨</blockquote>",
            bot=bot,
        )
        return

    ok = await act_and_reply(
        message,
        bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **grant),
        card("⬆️", "Promoted", target.first_name or "User", f"\n🏷️ {esc(title)}" if title else ""),
        None,
        "Couldn't promote",
        bot=bot,
    )
    if not ok:
        return
    invalidate(chat_id, target.id, bot_id=bot.id)
    if title:
        try:
            await bot.set_chat_administrator_custom_title(
                chat_id=chat_id,
                user_id=target.id,
                custom_title=title,
            )
        except Exception:
            pass


@router.message(Command("demote"))
async def demote_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    target = await gate(message, bot, pool, False, "can_promote_members")
    if target is None:
        return
    chat_id = message.chat.id
    m = await ax_common_member(bot, chat_id, target.id)
    if target.id == bot.id or (m is not None and m.status == ChatMemberStatus.CREATOR):
        await say(
            message,
            "🛡️ <b>Protected</b>\n<blockquote>I can't demote that one ✨</blockquote>",
            bot=bot,
        )
        return
    if m is None or m.status != ChatMemberStatus.ADMINISTRATOR:
        await say(
            message,
            "💫 <b>Not an admin</b>\n<blockquote>That user isn't an admin ✨</blockquote>",
            bot=bot,
        )
        return

    ok = await act_and_reply(
        message,
        bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            can_manage_chat=False,
            can_restrict_members=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_change_info=False,
            can_manage_video_chats=False,
            can_promote_members=False,
            can_manage_topics=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        ),
        card("⬇️", "Demoted", target.first_name or "User"),
        None,
        "Couldn't demote",
        "\n<i>I can only demote admins that I promoted.</i>",
        bot=bot,
    )
    if ok:
        invalidate(chat_id, target.id, bot_id=bot.id)


def make_temp(kind: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
        target = await gate(message, bot, pool)
        if target is None:
            return
        args = cmd_args(message)
        d = args if message.reply_to_message else args[1:]
        secs = parse_dur(d[0]) if d else None
        if not secs:
            await say(
                message,
                f"⏳ <b>Add a time</b>\n<blockquote>Example: /t{kind} 30m spamming\nUnits: s, m, h, d, w</blockquote>",
                bot=bot,
            )
            return
        reason = " ".join(d[1:]) or None
        until = datetime.now(timezone.utc) + timedelta(seconds=secs)
        chat_id = message.chat.id
        bid = bot_ctx.bot_id
        if kind == "mute":
            action = bot.restrict_chat_member(chat_id, target.id, permissions=MUTED, until_date=until)
        else:
            action = bot.ban_chat_member(chat_id, target.id, until_date=until)

        icon, title = META[kind]
        extra = f"\n⏳ For <b>{esc(d[0])}</b>" + (f"\n📝 {esc(reason)}" if reason else "")
        undo, label = UNDO[kind]
        ok = await act_and_reply(
            message,
            action,
            card(icon, title, target.first_name or "User", extra),
            InlineKeyboardMarkup(
                inline_keyboard=[[btn(label, f"axm:{undo}:{target.id}", style="success")]]
            ),
            bot=bot,
        )
        if not ok:
            return

        async def _save() -> None:
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ax_temp_punish (bot_id, chat_id, user_id, ptype, expires_at, reason) "
                    "VALUES ($1,$2,$3,$4,$5,$6) "
                    "ON CONFLICT (chat_id, user_id, ptype) DO UPDATE SET bot_id=$1, expires_at=$5, reason=$6",
                    bid,
                    chat_id,
                    target.id,
                    kind,
                    until,
                    reason,
                )
            NEED[bid] = True

        NEED[bid] = True
        fire(_save())
        fire(_log_action(bot, pool, chat_id, message.from_user, f"t{kind}", target, extra))

    return cmd


async def check_expired(bot: Bot, pool: asyncpg.Pool, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id if bot_id is not None else bot.id)
    if not NEED.get(bid, True):
        return
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT chat_id, user_id, ptype FROM ax_temp_punish "
                "WHERE bot_id=$1 AND expires_at <= now()",
                bid,
            )
        for r in rows:
            try:
                await perform(
                    "unmute" if r["ptype"] == "mute" else "unban",
                    bot,
                    pool,
                    r["chat_id"],
                    r["user_id"],
                )
            except Exception:
                await clear_timer(pool, r["chat_id"], r["user_id"], r["ptype"], bot_id=bid)
        async with pool.acquire() as c:
            if not await c.fetchval("SELECT count(*) FROM ax_temp_punish WHERE bot_id=$1", bid):
                NEED[bid] = False
    except Exception:
        log.exception("check_expired job failed")


for _name in ("mute", "unmute", "ban", "unban", "kick"):
    router.message(Command(_name))(make_cmd(_name))

router.message(Command("tmute"))(make_temp("mute"))
router.message(Command("tban"))(make_temp("ban"))


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    pool: asyncpg.Pool = dispatcher["pool"]
    bid = bot_context.resolve_bot_id(bot.id)
    NEED[bid] = True
    scheduler.add_job(
        check_expired,
        trigger="interval",
        seconds=300,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
        args=[bot, pool, bid],
        id=f"ax_mod:check_expired:{bid}",
        replace_existing=True,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

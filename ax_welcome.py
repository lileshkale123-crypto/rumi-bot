"""Welcome and goodbye messages (aiogram 3.x)."""
import logging

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import LinkPreviewOptions, Message

import ax_fill
import ax_rules
from ax_common import fire, get_bot_username, is_admin

log = logging.getLogger(__name__)
router = Router(name="ax_welcome")

IMG = "assets/rumi-start-v2.png"
DEFAULTS = {
    "welcome": "✨ <b>Welcome, {first_name}!</b>\n<blockquote>Glad to have you in <b>{chat_name}</b> 💫\nBe kind and enjoy your stay 🌸</blockquote>",
    "goodbye": "👋 <b>Goodbye, {first_name}</b>\n<blockquote>See you again in <b>{chat_name}</b> 💫</blockquote>",
}
_VALID_KINDS = ("welcome", "goodbye")
_VALID_COLS = ("text", "enabled", "photo")


async def get(pool: asyncpg.Pool, chat_id: int, kind: str):
    assert kind in _VALID_KINDS
    async with pool.acquire() as c:
        r = await c.fetchrow(
            f"SELECT {kind}_text t, {kind}_enabled e, {kind}_photo p FROM ax_settings WHERE chat_id=$1",
            chat_id,
        )
    return (r["t"], r["e"], r["p"]) if r else (None, True, None)


async def get_full(pool: asyncpg.Pool, chat_id: int, kind: str):
    """Everything the welcome needs in a single query."""
    assert kind in _VALID_KINDS
    async with pool.acquire() as c:
        r = await c.fetchrow(
            f"SELECT {kind}_text t, {kind}_enabled e, {kind}_photo p, "
            f"clean_welcome cw, last_welcome_id lw FROM ax_settings WHERE chat_id=$1",
            chat_id,
        )
    if r:
        return r["t"], r["e"], r["p"], r["cw"], r["lw"]
    return None, True, None, None, None


async def _quiet_delete(bot: Bot, chat_id: int, msg_id: int) -> None:
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


async def put(pool: asyncpg.Pool, chat_id: int, kind: str, col: str, value) -> None:
    assert kind in _VALID_KINDS and col in _VALID_COLS
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_settings (chat_id, {kind}_{col}) VALUES ($1,$2) "
            f"ON CONFLICT (chat_id) DO UPDATE SET {kind}_{col}=$2",
            chat_id,
            value,
        )


def make_set(kind: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        chat = message.chat
        user = message.from_user
        if user is None:
            return

        async def reply_html(t: str):
            return await message.reply(t, parse_mode=ParseMode.HTML)

        if chat.type == "private" or not await is_admin(bot, chat.id, user.id, "can_change_info"):
            await reply_html(
                "🛡️ <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>"
            )
            return

        raw_msg = message.text or message.caption or ""
        parts = raw_msg.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        rp = message.reply_to_message
        photo = message.photo[-1] if message.photo else (rp.photo[-1] if rp and rp.photo else None)
        if not arg and rp:
            arg = (rp.text or rp.caption or "").strip()
        low = arg.lower()

        if low in ("on", "off") and not message.photo and not (rp and rp.photo):
            await put(pool, chat.id, kind, "enabled", low == "on")
            await reply_html(f"{'✅' if low == 'on' else '🚫'} <b>{kind.title()} {low}</b>")
        elif low == "reset" and not message.photo and not (rp and rp.photo):
            await put(pool, chat.id, kind, "text", None)
            await put(pool, chat.id, kind, "photo", None)
            await reply_html(f"🔄 <b>{kind.title()} reset</b>\n<blockquote>Back to the default ✨</blockquote>")
        elif low in ("clearphoto", "nophoto", "rmphoto"):
            await put(pool, chat.id, kind, "photo", None)
            await reply_html(
                f"🖼️ <b>{kind.title()} photo removed</b>\n<blockquote>Now using text only ✨</blockquote>"
            )
        elif not arg and not photo:
            t, e, p = await get(pool, chat.id, kind)
            await reply_html(
                f"📖 <b>{kind.title()} settings</b>\n<blockquote>Status: <b>{'on' if e else 'off'}</b>\n"
                f"Text: <b>{'custom' if t else 'default'}</b>\nPhoto: <b>{'custom' if p else 'default'}</b></blockquote>\n"
                f"<b>Usage</b>\n<blockquote>• /set{kind} your text + buttons\n"
                f"• Send a photo with /set{kind} caption\n"
                f"• Reply to a long text or photo with /set{kind}\n"
                f"• /set{kind} clearphoto | on | off | reset\n"
                f"• Tags: {{first}} {{mention}} {{chatname}} {{rules}}</blockquote>"
            )
        else:
            done = []
            if arg:
                await put(pool, chat.id, kind, "text", arg)
                done.append("text")
            if photo:
                await put(pool, chat.id, kind, "photo", photo.file_id)
                done.append("photo")
            await put(pool, chat.id, kind, "enabled", True)
            _, rows, _ = ax_fill.parse(arg) if arg else ("", [], {})
            btn_count = sum(len(r) for r in rows)
            btn_info = f" ({btn_count} button{'s' if btn_count != 1 else ''})" if btn_count else ""
            await reply_html(
                f"✅ <b>{kind.title()} updated</b>\n<blockquote>{' + '.join(done)}{btn_info} saved 💫</blockquote>"
            )

    return cmd


async def announce(bot: Bot, pool: asyncpg.Pool, chat, kind: str, member) -> None:
    if member.id == bot.id:
        return
    text, enabled, photo, cw, lw = await get_full(pool, chat.id, kind)
    if not enabled:
        return

    raw = ax_fill.pick(text or DEFAULTS[kind])
    want_rules = ax_fill.has_rules(raw)
    rules_same = "{rules:same}" in raw
    body, rows, flags = ax_fill.parse(raw)

    rules_url = None
    rules_label = None
    un = await get_bot_username(bot)
    if want_rules and un:
        r_cfg = await ax_rules.get(pool, chat.id)
        rules_url = f"https://t.me/{un}?start=rules_{chat.id}"
        rules_label = r_cfg.get("button_text") or "📜 Rules"

    body = ax_fill.fill(body, member, chat, rules_link=rules_url)
    kb = ax_fill.keyboard(
        rows,
        rules_url=rules_url,
        rules_same=rules_same,
        note_url=(lambda n: f"https://t.me/{un}?start=note_{chat.id}_{n.lower()}") if un else None,
        rules_label=rules_label,
    )

    kw = {
        "disable_notification": flags["nonotif"],
        "protect_content": flags["protect"],
    }
    if kb:
        kw["reply_markup"] = kb

    if kind == "welcome" and cw and lw:
        fire(_quiet_delete(bot, chat.id, lw))  # deleted while the new welcome is being sent

    async def _send(pm):
        # Telegram caps photo captions at 1024 chars. If longer, send photo then full text+buttons.
        if photo and len(body) <= 1024:
            return await bot.send_photo(
                chat.id,
                photo,
                caption=body or None,
                parse_mode=pm,
                has_spoiler=flags["mediaspoiler"],
                **kw,
            )
        if photo and len(body) > 1024:
            try:
                await bot.send_photo(
                    chat.id,
                    photo,
                    has_spoiler=flags["mediaspoiler"],
                    disable_notification=flags["nonotif"],
                    protect_content=flags["protect"],
                )
            except Exception:
                pass
            return await bot.send_message(
                chat.id,
                body[:4096],
                parse_mode=pm,
                link_preview_options=LinkPreviewOptions(is_disabled=not flags["preview"]),
                **kw,
            )
        return await bot.send_message(
            chat.id,
            (body or "✨")[:4096],
            parse_mode=pm,
            link_preview_options=LinkPreviewOptions(is_disabled=not flags["preview"]),
            **kw,
        )

    sent = None
    try:
        sent = await _send(ParseMode.HTML)
    except TelegramBadRequest:
        try:
            sent = await _send(None)
        except Exception:
            sent = None
    except Exception:
        sent = None

    if kind == "welcome" and sent:
        try:
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ax_settings (chat_id, last_welcome_id) VALUES ($1,$2) "
                    "ON CONFLICT (chat_id) DO UPDATE SET last_welcome_id=$2",
                    chat.id,
                    sent.message_id,
                )
        except Exception:
            pass


@router.message(F.new_chat_members)
async def on_join(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    log.debug(
        "WELCOME_JOIN msg=%s new=%s",
        message.message_id,
        [u.id for u in (message.new_chat_members or [])],
    )
    for m in message.new_chat_members or []:
        await announce(bot, pool, message.chat, "welcome", m)


@router.message(F.left_chat_member)
async def on_leave(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    m = message.left_chat_member
    if m:
        await announce(bot, pool, message.chat, "goodbye", m)


for _kind in ("welcome", "goodbye"):
    router.message(Command(f"set{_kind}"))(make_set(_kind))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

"""Captcha settings commands (aiogram 3.x)."""
import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import ax_captcha
from ax_captcha import settings
from ax_common import cmd_args, esc, is_admin
from ax_warn2 import fmt_time, parse_time

router = Router(name="ax_captcha2")
H = ParseMode.HTML
_COLS = ("enabled", "mode", "mute_secs", "kick", "kick_secs", "rules", "btn_text")


async def _ok(message: Message, bot: Bot) -> bool:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False
    if chat.type == "private" or not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await message.reply(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>",
            parse_mode=H,
        )
        return False
    return True


async def _set(pool: asyncpg.Pool, chat_id: int, col: str, val) -> None:
    assert col in _COLS
    ax_captcha._SC.pop(chat_id, None)
    async with pool.acquire() as c:
        await c.execute(
            f"INSERT INTO ax_captcha_settings (chat_id, {col}) VALUES "
            f"($1,$2) ON CONFLICT (chat_id) DO UPDATE SET {col}=$2",
            chat_id,
            val,
        )


def _yn(a: str) -> bool | None:
    a = (a or "").lower()
    if a in ("on", "yes", "y", "true"):
        return True
    if a in ("off", "no", "n", "false"):
        return False
    return None


@router.message(Command("captcha"))
async def captcha(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _ok(message, bot):
        return
    chat = message.chat
    args = cmd_args(message)
    v = _yn(args[0] if args else "")
    s = await settings(pool, chat.id)
    if v is None:
        await message.reply(
            f"🔐 <b>Captcha</b>\n<blockquote>Status: <b>"
            f"{'on' if s['enabled'] else 'off'}</b>\nMode: "
            f"<b>{s['mode']}</b>\nKick: <b>{'on' if s['kick'] else 'off'}"
            f"</b> after <b>{fmt_time(s['kick_secs'])}</b>\nMute after "
            f"solving: <b>{fmt_time(s['mute_secs']) if s['mute_secs'] else 'no'}"
            f"</b>\nUse /captcha on or off</blockquote>",
            parse_mode=H,
        )
        return
    await _set(pool, chat.id, "enabled", v)
    await message.reply(
        f"{'✅' if v else '🚫'} <b>Captcha {'on' if v else 'off'}</b>",
        parse_mode=H,
    )


@router.message(Command("captchamode"))
async def captchamode(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _ok(message, bot):
        return
    args = cmd_args(message)
    a = args[0].lower() if args else ""
    if a not in ("button", "math", "text"):
        await message.reply(
            "🔐 <b>Captcha mode</b>\n<blockquote>/captchamode button, math or text</blockquote>",
            parse_mode=H,
        )
        return
    await _set(pool, message.chat.id, "mode", a)
    await message.reply(f"✅ <b>Mode: {a}</b>", parse_mode=H)


def _flag(col: str, title: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        if not await _ok(message, bot):
            return
        args = cmd_args(message)
        v = _yn(args[0] if args else "")
        if v is None:
            await message.reply(f"<blockquote>Use /{title} on or off</blockquote>", parse_mode=H)
            return
        await _set(pool, message.chat.id, col, v)
        await message.reply(
            f"{'✅' if v else '🚫'} <b>{title} {'on' if v else 'off'}</b>",
            parse_mode=H,
        )

    return cmd


def _time(col: str, title: str, allow_zero: bool):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        if not await _ok(message, bot):
            return
        args = cmd_args(message)
        a = args[0] if args else ""
        secs = 0 if (allow_zero and a == "0") else parse_time(a)
        if secs is None or (secs == 0 and not allow_zero):
            await message.reply(
                f"<blockquote>Use /{title} 5m (s, m, h, d, w)"
                f"{' or 0 for forever' if allow_zero else ''}</blockquote>",
                parse_mode=H,
            )
            return
        await _set(pool, message.chat.id, col, int(secs))
        await message.reply(
            f"✅ <b>{title}: {fmt_time(secs) if secs else 'off'}</b>",
            parse_mode=H,
        )

    return cmd


@router.message(Command("setcaptchatext"))
async def setcaptchatext(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _ok(message, bot):
        return
    t = " ".join(cmd_args(message)).strip()
    if not t:
        await message.reply("<blockquote>/setcaptchatext your button text</blockquote>", parse_mode=H)
        return
    await _set(pool, message.chat.id, "btn_text", t[:60])
    await message.reply(
        f"✅ <b>Button text saved</b>\n<blockquote>{esc(t[:60])}</blockquote>",
        parse_mode=H,
    )


@router.message(Command("resetcaptchatext"))
async def resetcaptchatext(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _ok(message, bot):
        return
    await _set(pool, message.chat.id, "btn_text", None)
    await message.reply("🔄 <b>Button text reset</b>", parse_mode=H)


router.message(Command("captcharules"))(_flag("rules", "captcharules"))
router.message(Command("captchakick"))(_flag("kick", "captchakick"))
router.message(Command("captchamutetime"))(_time("mute_secs", "captchamutetime", True))
router.message(Command("captchakicktime"))(_time("kick_secs", "captchakicktime", False))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

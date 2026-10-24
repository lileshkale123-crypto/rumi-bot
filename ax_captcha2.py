"""Captcha settings commands."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import esc, is_admin
from ax_captcha import settings
from ax_welcome import get as get_welcome
from ax_warn2 import parse_time, fmt_time

H = ParseMode.HTML


async def _ok(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private" or not await is_admin(
            context.bot, chat.id, user.id):
        await update.effective_message.reply_text(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group "
            "as an admin ✨</blockquote>", parse_mode=H)
        return False
    return True


async def _set(context, chat_id, col, val):
    async with context.bot_data["pool"].acquire() as c:
        await c.execute(
            f"INSERT INTO ax_captcha_settings (chat_id, {col}) VALUES "
            f"($1,$2) ON CONFLICT (chat_id) DO UPDATE SET {col}=$2",
            chat_id, val)


def _yn(a):
    a = (a or "").lower()
    if a in ("on", "yes", "y", "true"):
        return True
    if a in ("off", "no", "n", "false"):
        return False
    return None


async def captcha(update, context):
    if not await _ok(update, context):
        return
    msg, chat = update.effective_message, update.effective_chat
    v = _yn(context.args[0] if context.args else "")
    s = await settings(context.bot_data["pool"], chat.id)
    if v is None:
        await msg.reply_text(
            f"🔐 <b>Captcha</b>\n<blockquote>Status: <b>"
            f"{'on' if s['enabled'] else 'off'}</b>\nMode: "
            f"<b>{s['mode']}</b>\nKick: <b>{'on' if s['kick'] else 'off'}"
            f"</b> after <b>{fmt_time(s['kick_secs'])}</b>\nMute after "
            f"solving: <b>{fmt_time(s['mute_secs']) if s['mute_secs'] else 'no'}"
            f"</b>\nUse /captcha on or off</blockquote>", parse_mode=H)
        return
    await _set(context, chat.id, "enabled", v)
    await msg.reply_text(
        f"{'✅' if v else '🚫'} <b>Captcha {'on' if v else 'off'}</b>",
        parse_mode=H)


async def captchamode(update, context):
    if not await _ok(update, context):
        return
    a = (context.args[0].lower() if context.args else "")
    if a not in ("button", "math", "text"):
        await update.effective_message.reply_text(
            "🔐 <b>Captcha mode</b>\n<blockquote>/captchamode button, "
            "math or text</blockquote>", parse_mode=H)
        return
    await _set(context, update.effective_chat.id, "mode", a)
    await update.effective_message.reply_text(
        f"✅ <b>Mode: {a}</b>", parse_mode=H)


def _flag(col, title):
    async def cmd(update, context):
        if not await _ok(update, context):
            return
        v = _yn(context.args[0] if context.args else "")
        if v is None:
            await update.effective_message.reply_text(
                f"<blockquote>Use /{title} on or off</blockquote>",
                parse_mode=H)
            return
        await _set(context, update.effective_chat.id, col, v)
        await update.effective_message.reply_text(
            f"{'✅' if v else '🚫'} <b>{title} {'on' if v else 'off'}</b>",
            parse_mode=H)
    return cmd


def _time(col, title, allow_zero):
    async def cmd(update, context):
        if not await _ok(update, context):
            return
        a = context.args[0] if context.args else ""
        secs = 0 if (allow_zero and a == "0") else parse_time(a)
        if secs is None or (secs == 0 and not allow_zero):
            await update.effective_message.reply_text(
                f"<blockquote>Use /{title} 5m (s, m, h, d, w)"
                f"{' or 0 for forever' if allow_zero else ''}</blockquote>",
                parse_mode=H)
            return
        await _set(context, update.effective_chat.id, col, int(secs))
        await update.effective_message.reply_text(
            f"✅ <b>{title}: {fmt_time(secs) if secs else 'off'}</b>",
            parse_mode=H)
    return cmd


async def setcaptchatext(update, context):
    if not await _ok(update, context):
        return
    t = " ".join(context.args).strip()
    if not t:
        await update.effective_message.reply_text(
            "<blockquote>/setcaptchatext your button text</blockquote>",
            parse_mode=H)
        return
    await _set(context, update.effective_chat.id, "btn_text", t[:60])
    await update.effective_message.reply_text(
        f"✅ <b>Button text saved</b>\n<blockquote>{esc(t[:60])}"
        f"</blockquote>", parse_mode=H)


async def resetcaptchatext(update, context):
    if not await _ok(update, context):
        return
    await _set(context, update.effective_chat.id, "btn_text", None)
    await update.effective_message.reply_text(
        "🔄 <b>Button text reset</b>", parse_mode=H)


def register(app):
    for n, f in (("captcha", captcha), ("captchamode", captchamode),
                 ("captcharules", _flag("rules", "captcharules")),
                 ("captchakick", _flag("kick", "captchakick")),
                 ("captchamutetime", _time("mute_secs", "captchamutetime", True)),
                 ("captchakicktime", _time("kick_secs", "captchakicktime", False)),
                 ("setcaptchatext", setcaptchatext),
                 ("resetcaptchatext", resetcaptchatext)):
        app.add_handler(CommandHandler(n, f))


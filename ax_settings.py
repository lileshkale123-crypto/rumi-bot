"""Flood protection and the settings menu."""
import time

from telegram import ChatPermissions, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

import ax_welcome
from ax_common import btn, esc, is_admin

H = ParseMode.HTML
_cache, _hits = {}, {}


async def cfg(pool, chat_id):
    c = _cache.get(chat_id)
    if c and time.time() - c[0] < 30:
        return c[1]
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT flood_limit, flood_window, flood_mute FROM ax_settings WHERE chat_id=$1", chat_id)
    v = tuple(r) if r else (0, 5, 300)
    _cache[chat_id] = (time.time(), v)
    return v


async def set_limit(pool, chat_id, n):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ax_settings (chat_id, flood_limit) VALUES ($1,$2) "
            "ON CONFLICT (chat_id) DO UPDATE SET flood_limit=$2", chat_id, n)
    _cache.pop(chat_id, None)


async def flood_watcher(update, context):
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if not msg or not user or user.is_bot:
        return
    limit, window, mute = await cfg(context.bot_data["pool"], chat.id)
    if limit == 0:
        return
    now, key = time.time(), (chat.id, user.id)
    hits = [t for t in _hits.get(key, []) if now - t < window] + [now]
    _hits[key] = hits
    if len(hits) < limit:
        return
    _hits[key] = []
    if await is_admin(context.bot, chat.id, user.id):
        return
    try:
        await context.bot.restrict_chat_member(
            chat.id, user.id, permissions=ChatPermissions(can_send_messages=False),
            until_date=int(now) + mute)
        await msg.reply_text(
            f"🚫 <b>Slow down</b>\n<blockquote>👤 <b>{esc(user.first_name)}</b> was muted for "
            f"<b>{mute // 60} min</b> for sending too fast 💫</blockquote>", parse_mode=H)
    except Exception:
        pass


def nav(*rows):
    home = [btn("⬅ Back", "axs:main"), btn("🏠 Home", "axs:main")]
    return InlineKeyboardMarkup([*rows, home])


def main_view():
    text = "⚙️ <b>Rumi Settings</b>\n<blockquote>Pick a category to customize your group 💫</blockquote>"
    kb = InlineKeyboardMarkup([
        [btn("🌊 Flood Protection", "axs:flood", style="primary")],
        [btn("👋 Welcome", "axs:welcome", style="primary"), btn("😢 Goodbye", "axs:goodbye", style="primary")],
        [btn("✖ Close", "axs:close", style="danger")],
    ])
    return text, kb


async def flood_view(pool, chat_id):
    limit, window, mute = await cfg(pool, chat_id)
    status = "🔴 Off" if limit == 0 else f"🟢 On: {limit} msgs / {window}s, mute {mute // 60}m"
    text = f"🌊 <b>Flood Protection</b>\n<blockquote>Status: <b>{status}</b>\nCustom value: /setflood 8</blockquote>"
    return text, nav(
        [btn("🟢 On (6)", "axs:flood:on:6", style="success"), btn("🟢 On (10)", "axs:flood:on:10", style="success")],
        [btn("🔴 Turn off", "axs:flood:off", style="danger")])


async def msg_view(pool, chat_id, kind):
    t, e, p = await ax_welcome.get(pool, chat_id, kind)
    icon = "👋" if kind == "welcome" else "😢"
    text = (f"{icon} <b>{kind.title()} Message</b>\n<blockquote>Status: <b>{'🟢 On' if e else '🔴 Off'}</b>\n"
            f"Text: <b>{'custom' if t else 'default'}</b>\nPhoto: <b>{'custom' if p else 'default'}</b>\n"
            f"Customize: /set{kind}</blockquote>")
    return text, nav(
        [btn("🟢 Enable", f"axs:{kind}:on", style="success"), btn("🔴 Disable", f"axs:{kind}:off", style="danger")],
        [btn("🔄 Reset to default", f"axs:{kind}:reset", style="primary")])


async def settings_cmd(update, context):
    chat = update.effective_chat
    if chat.type == "private" or not await is_admin(context.bot, chat.id, update.effective_user.id):
        await update.effective_message.reply_text(
            "🛡️ <b>Admins only</b>\n<blockquote>Open this in your group as an admin ✨</blockquote>", parse_mode=H)
        return
    text, kb = main_view()
    await update.effective_message.reply_text(text, parse_mode=H, reply_markup=kb)


async def setflood_cmd(update, context):
    chat, msg = update.effective_chat, update.effective_message
    if chat.type == "private" or not await is_admin(context.bot, chat.id, update.effective_user.id):
        await msg.reply_text("🛡️ <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>", parse_mode=H)
        return
    pool = context.bot_data["pool"]
    a = (context.args or [""])[0].lower()
    if a == "off":
        await set_limit(pool, chat.id, 0)
        await msg.reply_text("🔴 <b>Flood protection off</b>", parse_mode=H)
    elif a.isdigit() and int(a) >= 3:
        await set_limit(pool, chat.id, int(a))
        limit, window, _ = await cfg(pool, chat.id)
        await msg.reply_text(f"🟢 <b>Flood protection on</b>\n<blockquote>{a} messages in {window}s triggers a mute 💫</blockquote>", parse_mode=H)
    else:
        text, kb = await flood_view(pool, chat.id)
        await msg.reply_text(text, parse_mode=H, reply_markup=kb)


async def settings_callback(update, context):
    q = update.callback_query
    chat = q.message.chat
    if not await is_admin(context.bot, chat.id, q.from_user.id):
        await q.answer("Admins only 🛡️", show_alert=True)
        return
    pool = context.bot_data["pool"]
    p = q.data.split(":")
    sec, act = p[1], (p[2] if len(p) > 2 else None)
    if sec == "close":
        await q.answer()
        await q.message.delete()
        return
    if sec == "flood":
        if act == "on":
            await set_limit(pool, chat.id, int(p[3]))
        elif act == "off":
            await set_limit(pool, chat.id, 0)
        text, kb = await flood_view(pool, chat.id)
    elif sec in ("welcome", "goodbye"):
        if act in ("on", "off"):
            await ax_welcome.put(pool, chat.id, sec, "enabled", act == "on")
        elif act == "reset":
            await ax_welcome.put(pool, chat.id, sec, "text", None)
            await ax_welcome.put(pool, chat.id, sec, "photo", None)
        text, kb = await msg_view(pool, chat.id, sec)
    else:
        text, kb = main_view()
    await q.answer("Done ✨" if act else None)
    try:
        await q.edit_message_text(text, parse_mode=H, reply_markup=kb)
    except Exception:
        pass


def register(app):
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("setflood", setflood_cmd))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="^axs:"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.PRIVATE, flood_watcher), group=-4)

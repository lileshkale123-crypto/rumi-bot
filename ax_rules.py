"""Rules: /rules /setrules /privaterules /resetrules /setrulesbutton /resetrulesbutton"""
from telegram.ext import CommandHandler

import ax_mod
from ax_common import esc, is_admin

DEFAULT = "No rules have been set yet. Admins can add them with /setrules"
COLS = ("rules_text", "private", "button_text")


async def get(pool, chat_id):
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM ax_rules WHERE chat_id=$1", chat_id)
    return dict(r) if r else {"rules_text": None, "private": False, "button_text": None}


async def _set(pool, chat_id, col, val):
    assert col in COLS
    async with pool.acquire() as c:
        await c.execute(f"INSERT INTO ax_rules (chat_id, {col}) VALUES ($1,$2) "
                        f"ON CONFLICT (chat_id) DO UPDATE SET {col}=$2", chat_id, val)


async def _gate(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>")
        return False
    if not await is_admin(context.bot, chat.id, user.id):
        await ax_mod.say(update, "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>")
        return False
    return True


def _body(update):
    parts = (update.effective_message.text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def rules_cmd(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat.type == "private":
        await ax_mod.say(update, "⚠️ <b>Groups only</b>\n<blockquote>Use /rules inside a group 💫</blockquote>")
        return
    r = await get(context.bot_data["pool"], chat.id)
    text = r["rules_text"] or DEFAULT
    if (context.args or [""])[0].lower() == "noformat":
        await update.effective_message.reply_text(text)
        return
    card = f"📜 <b>Rules of {esc(chat.title or 'this group')}</b>\n<blockquote>{esc(text)}</blockquote>"
    if r["private"]:
        try:
            await context.bot.send_message(user.id, card, parse_mode="HTML")
            await ax_mod.say(update, "📩 <b>Rules sent</b>\n<blockquote>Check your private chat with me 💜</blockquote>")
        except Exception:
            await ax_mod.say(update, "💫 <b>Open a chat with me first</b>\n<blockquote>Send me /start in private, then try /rules again</blockquote>")
        return
    await ax_mod.say(update, card)


async def setrules_cmd(update, context):
    if not await _gate(update, context):
        return
    body = _body(update)
    if not body and update.effective_message.reply_to_message:
        body = update.effective_message.reply_to_message.text or ""
    if not body:
        await ax_mod.say(update, "💫 <b>Rules?</b>\n<blockquote>Example: /setrules 1. Be kind\n2. No spam</blockquote>")
        return
    await _set(context.bot_data["pool"], update.effective_chat.id, "rules_text", body)
    await ax_mod.say(update, "✅ <b>Rules saved</b>\n<blockquote>Members can read them with /rules 📜</blockquote>")


async def resetrules_cmd(update, context):
    if not await _gate(update, context):
        return
    await _set(context.bot_data["pool"], update.effective_chat.id, "rules_text", None)
    await ax_mod.say(update, "✅ <b>Rules cleared</b>\n<blockquote>Back to the default message</blockquote>")


async def privaterules_cmd(update, context):
    if not await _gate(update, context):
        return
    pool, chat_id = context.bot_data["pool"], update.effective_chat.id
    arg = (context.args or [""])[0].lower()
    if arg not in ("on", "off", "yes", "no"):
        r = await get(pool, chat_id)
        await ax_mod.say(update, f"⚙️ <b>Private rules</b>\n<blockquote>Currently <b>{'on' if r['private'] else 'off'}</b>\nUse /privaterules on or off</blockquote>")
        return
    val = arg in ("on", "yes")
    await _set(pool, chat_id, "private", val)
    await ax_mod.say(update, f"✅ <b>Private rules {'on' if val else 'off'}</b>\n<blockquote>/rules now goes {'to their PM' if val else 'in the group'}</blockquote>")


async def setbutton_cmd(update, context):
    if not await _gate(update, context):
        return
    body = _body(update)
    if not body:
        await ax_mod.say(update, "💫 <b>Button text?</b>\n<blockquote>Example: /setrulesbutton Read the rules</blockquote>")
        return
    await _set(context.bot_data["pool"], update.effective_chat.id, "button_text", body[:40])
    await ax_mod.say(update, "✅ <b>Rules button renamed</b>\n<blockquote>Used by the {rules} tag in welcomes 💜</blockquote>")


async def resetbutton_cmd(update, context):
    if not await _gate(update, context):
        return
    await _set(context.bot_data["pool"], update.effective_chat.id, "button_text", None)
    await ax_mod.say(update, "✅ <b>Button name reset</b>\n<blockquote>Back to the default</blockquote>")


def register(app):
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(CommandHandler("setrules", setrules_cmd))
    app.add_handler(CommandHandler("resetrules", resetrules_cmd))
    app.add_handler(CommandHandler("privaterules", privaterules_cmd))
    app.add_handler(CommandHandler("setrulesbutton", setbutton_cmd))
    app.add_handler(CommandHandler("resetrulesbutton", resetbutton_cmd))

"""Admin command: toggle all fun/economy/game commands off or on in one shot."""
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import is_admin

FUN_CMDS = ["work", "spin", "rob", "pay", "guard", "bluff",
            "mafia", "stopmafia", "ship", "match", "couple", "kill",
            "hug", "kiss", "pat", "slap", "punch", "bite",
            "poke", "wink", "cuddle", "bonk", "murder",
            "love", "crush", "couples", "look", "brain", "stupid_meter"]
H = ParseMode.HTML


async def funmode_cmd(update, context):
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None or chat.type == "private":
        return
    if not await is_admin(context.bot, chat.id, user.id):
        await update.effective_message.reply_text(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>",
            parse_mode=H)
        return
    args = context.args or []
    if not args or args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text("Usage: /funmode off  or  /funmode on")
        return
    on = args[0].lower() == "on"
    pool = context.bot_data["pool"]
    async with pool.acquire() as c:
        if on:
            await c.execute(
                "DELETE FROM ax_disabled WHERE chat_id=$1 AND cmd = ANY($2::text[])",
                chat.id, FUN_CMDS)
        else:
            for cmd in FUN_CMDS:
                await c.execute(
                    "INSERT INTO ax_disabled (chat_id, cmd) VALUES ($1,$2) "
                    "ON CONFLICT DO NOTHING", chat.id, cmd)
    import ax_disable
    ax_disable._cache.pop(chat.id, None)
    if on:
        await update.effective_message.reply_text(
            "✅ <b>Fun mode ON</b>\n<blockquote>Economy and games are back.</blockquote>", parse_mode=H)
    else:
        await update.effective_message.reply_text(
            "🔕 <b>Fun mode OFF</b>\n<blockquote>Economy and games are disabled. "
            "Moderation still works. Use <code>/funmode on</code> to bring them back.</blockquote>",
            parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("funmode", funmode_cmd))


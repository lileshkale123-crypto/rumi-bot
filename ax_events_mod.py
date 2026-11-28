"""Random group events (min 25 users, 1/day/group: /catch $2k, /guess 100-500) & Dot/Slash Mod (.promote 1/2/3/4, .warn, .mute, .ban, .kick, .pin, /admin, /report)."""
import asyncio, html, logging, random
from datetime import datetime, timedelta, timezone
import aiohttp, asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command
from aiogram.types import Message
import ax_mod, config
from ax_common import cmd_args, esc, invalidate, is_admin, resolve_target
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_events_mod")
H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))
_CATCH: dict[tuple[int, int], dict] = {}
_DOT_SEEN: dict[tuple[int, int, int], float] = {}

async def _do_promote(message: Message, bot: Bot, pool: asyncpg.Pool, raw_args: list[str]) -> None:
    # PROMOTE4_V1: same 4 levels as /promote
    await ax_mod.promote_tier(message, bot, pool, list(raw_args))

@router.message(Command("admin"))
async def admin_cmd(message: Message, bot: Bot) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    admins = [f'• <a href="tg://user?id={a.user.id}">{esc(a.user.first_name or "Admin")}</a>' for a in await bot.get_chat_administrators(message.chat.id) if not a.user.is_bot]
    await message.reply("👮 <b>ADMINS</b>\n<blockquote>" + ("\n".join(admins) or "None") + "</blockquote>", parse_mode=H)

@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.startswith("."))
async def dot_mod(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user or not message.text:
        return
    parts = message.text.strip()[1:].split()
    if not parts:
        return
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    dk = (bot.id, message.chat.id, message.message_id)
    now_l = asyncio.get_running_loop().time()
    if now_l - _DOT_SEEN.get(dk, -10.0) < 1.0:
        return
    _DOT_SEEN[dk] = now_l
    if cmd == "promote":
        await _do_promote(message, bot, pool, args)
    elif cmd == "pin" and message.reply_to_message and await is_admin(bot, message.chat.id, message.from_user.id, "can_pin_messages"):
        await bot.pin_chat_message(chat_id=message.chat.id, message_id=message.reply_to_message.message_id)
        await message.reply("📌 Pinned!")
    elif cmd == "warn":
        import ax_warn  # same warn system as /warn: counts warnings, punishes at the limit
        await ax_warn.make_warn("n")(message, bot, pool, bot_ctx)
    elif cmd in ("mute", "ban", "kick") and await is_admin(bot, message.chat.id, message.from_user.id, "can_restrict_members"):
        t = await resolve_target(message, bot, pool, args=args, bot_id=bot.id)
        if t and not getattr(t, "is_bot", False):
            if cmd != "warn":
                await ax_mod.perform(cmd, bot, pool, message.chat.id, t.id)
            await message.reply(f"✅ .{cmd} applied to <b>{esc(t.first_name or 'User')}</b>.", parse_mode=H)

def register(dp):
    if router.parent_router is None:
        dp.include_router(router)

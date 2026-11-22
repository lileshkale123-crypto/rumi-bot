"""Admin command: toggle all fun/economy/game commands off or on in one shot (aiogram 3.x)."""
import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from ax_common import cmd_args, is_admin

router = Router(name="ax_funmode")

FUN_CMDS = [
    "rob", "pay", "guard", "bluff", "dice", "bomb", "flip", "card", "roulette", "hack",
    "mafia", "stopmafia", "ship", "match", "couple", "kill",
    "hug", "kiss", "pat", "slap", "punch", "bite",
    "poke", "wink", "cuddle", "bonk", "murder",
    "love", "crush", "couples", "look", "brain", "stupid_meter",
]
H = ParseMode.HTML


@router.message(Command("funmode"))
async def funmode_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None or chat.type == "private":
        return
    if not await is_admin(bot, chat.id, user.id):
        await message.reply(
            "🛡 <b>Admins only</b>\n<blockquote>Use this in your group as an admin ✨</blockquote>",
            parse_mode=H,
        )
        return
    args = cmd_args(message)
    if not args or args[0].lower() not in ("on", "off"):
        await message.reply("Usage: /funmode off  or  /funmode on")
        return
    on = args[0].lower() == "on"
    async with pool.acquire() as c:
        if on:
            await c.execute(
                "DELETE FROM ax_disabled WHERE chat_id=$1 AND cmd = ANY($2::text[])",
                chat.id,
                FUN_CMDS,
            )
        else:
            await c.executemany(
                "INSERT INTO ax_disabled (chat_id, cmd) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                [(chat.id, cmd) for cmd in FUN_CMDS],
            )
    import ax_disable

    ax_disable._cache.pop(chat.id, None)
    if on:
        await message.reply(
            "✅ <b>Fun mode ON</b>\n<blockquote>Economy and games are back.</blockquote>",
            parse_mode=H,
        )
    else:
        await message.reply(
            "🔕 <b>Fun mode OFF</b>\n<blockquote>Economy and games are disabled. "
            "Moderation still works. Use <code>/funmode on</code> to bring them back.</blockquote>",
            parse_mode=H,
        )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

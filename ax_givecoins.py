"""Hidden owner-only /givecoins. Silent for everyone else (aiogram 3.x)."""
import asyncpg
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from ax_common import cmd_args, esc
from services import ensure_user_and_wallet

router = Router(name="ax_givecoins")

OWNERS = {8151412244, 6584534013}
MAX_AMT = 1_000_000_000


@router.message(Command("givecoins"))
async def givecoins_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None or user.id not in OWNERS:
        return
    args = cmd_args(message)
    try:
        amt = int(args[0])
    except (IndexError, ValueError):
        await message.reply("Usage: /givecoins amount (reply to someone, or alone for yourself)")
        return
    if amt < 1 or amt > MAX_AMT:
        await message.reply("Amount must be between 1 and %d" % MAX_AMT)
        return

    target = user
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("Bots have no wallet.")
        return

    await ensure_user_and_wallet(pool, target.id, target.username, target.first_name)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id = $2",
            amt,
            target.id,
        )
    name = esc(target.first_name or "Friend")
    await message.reply(
        "\U0001F4B0 <b>Done</b>\n<blockquote>%s received <b>%d</b> coins \u2728</blockquote>"
        % (name, amt),
        parse_mode=ParseMode.HTML,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

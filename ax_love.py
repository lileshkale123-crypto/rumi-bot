"""/ship, /match and /couple (aiogram 3.x, bot_id & brand aware)."""
import hashlib
import os
import random
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

import bot_context
import services
from ax_common import esc
from bot_context import BotContext, get_brand

router = Router(name="ax_love")

H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))
BOT_ID = None

TXT = {
    "ship": (
        ["💘 Random Ship", "💞 Cupid's Choice", "🎲 Lucky Pair", "🏹 Cupid Strikes", "💫 Destiny's Pick"],
        ["Cupid fired an arrow into the crowd...", "Fate just rolled the dice.", "Someone's blushing already...",
         "A brand-new ship has appeared.", "Two hearts, one random moment."],
        ["Every legendary story starts somewhere.", "This ship has officially set sail.",
         "Keep this screenshot, you never know.", "Let the comments begin!", "Wishing this pair good luck."],
        ["💔 Ship Sank", "🤝 Just Friends", "🌼 Cute Pair", "💕 Great Chemistry", "💍 Soulmates", "👑 Written in the Stars"],
    ),
    "match": (
        ["💖 Compatibility Report", "🔍 Match Scanner", "💞 Heart Sync Report", "🧬 Emotional Match", "🌟 Love Calculator"],
        ["Scanning emotional wavelengths...", "Calculating compatibility...", "Reading heart frequencies...",
         "Looking for hidden chemistry...", "Finalizing the report..."],
        ["Some bonds grow stronger with time.", "Trust the journey, not just the number.",
         "Hearts don't always follow logic.", "Maybe this is only the beginning.", "Fate might already be smiling."],
        ["❄️ No Chemistry", "🌱 Potential Friends", "😊 Good Vibes", "💙 Heart Sync", "💍 Soul Connection", "👑 Perfect Destiny"],
    ),
    "couple": (
        ["👑 Couple of the Day", "🌟 Today's Royal Couple", "💎 Hall of Romance", "✨ Couple Spotlight", "💖 Featured Couple"],
        ["Today's spotlight belongs to...", "The stars have made their decision.", "Love takes center stage today.",
         "The universe has spoken.", "Meet the stars of today's love story."],
        ["May your bond continue to shine.", "Wear this title with pride.", "Love wins once again.",
         "See you tomorrow for a new couple.", "The crown belongs to you today."],
        ["🥉 Rising Pair", "🥈 Star Couple", "🥇 Legendary Couple", "💍 Eternal Partners", "👑 Couple of Destiny", "🌌 Cosmic Soulmates"],
    ),
}

IMG = {
    "ship": "AgACAgUAAxkBAAIfs2rdxhiGFSb4OjQxVIGgo4XaLyJaAAJcGGsbk7zoVlsoLTzEqcUGAQADAgADeQADPQQ",
    "match": "couple_images/2.jpg",
    "couple": "couple_images/1.jpg",
    "crush": "AgACAgUAAxkBAAIftWrdxhxze0wav2Fd6HFa1oLCTmeWAAJdGGsbk7zoVpYixOnzgpPcAQADAgADeQADPQQ",
}
BAR = {"ship": ("💗", "█", "░"), "match": ("💙", "▰", "▱"), "couple": ("💜", "◆", "◇")}


def tier(kind: str, pct: int) -> str:
    i = 0 if pct <= 20 else 1 if pct <= 40 else 2 if pct <= 60 else 3 if pct <= 80 else 4 if pct <= 95 else 5
    return TXT[kind][3][i]


def score(a: int, b: int, day: str) -> int:
    x, y = sorted([a, b])
    return int(hashlib.md5(f"{x}-{y}-{day}".encode()).hexdigest(), 16) % 101


def card(kind: str, n1: str, n2: str, pct: int, extra: str = "") -> str:
    h, i, e, _ = TXT[kind]
    icon, f, o = BAR[kind]
    filled = max(0, min(10, round(pct / 10)))
    return (
        f"<b>{random.choice(h)}</b>\n<i>{random.choice(i)}</i>\n\n"
        f"<blockquote>👤 <b>{esc(n1)}</b>\n💞\n👤 <b>{esc(n2)}</b></blockquote>\n"
        f"{icon} {f * filled}{o * (10 - filled)} <b>{pct}%</b>\n{tier(kind, pct)}\n\n"
        f"<i>{random.choice(e)}</i>{extra}"
    )


async def send(message: Message, kind: str, text: str) -> None:
    img = IMG[kind]
    if img.startswith("AgAC"):
        try:
            await message.reply_photo(photo=img, caption=text, parse_mode=H)
            return
        except Exception:
            pass
    if os.path.exists(img):
        try:
            await message.reply_photo(photo=FSInputFile(img), caption=text, parse_mode=H)
            return
        except Exception:
            pass
    await message.reply(text, parse_mode=H)


async def groups_only(message: Message) -> bool:
    if message.chat.type == "private":
        await message.reply(
            "⚠️ <b>Groups only</b>\n<blockquote>Try this inside a group 💫</blockquote>",
            parse_mode=H,
        )
        return True
    return False


async def members(pool: asyncpg.Pool, chat_id: int, bot_id: int):
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT u.id, u.username, u.first_name FROM ax_chat_members m "
            "JOIN users u ON u.id = m.user_id AND u.bot_id = m.bot_id "
            "WHERE m.bot_id=$1 AND m.chat_id=$2 AND u.id<>$3",
            bot_id,
            chat_id,
            bot_id,
        )


@router.message(Command("ship"))
async def ship_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if await groups_only(message):
        return
    bid = bot_ctx.bot_id
    chat_id = message.chat.id
    now = datetime.now(timezone.utc)

    rows = await members(pool, chat_id, bid)
    if len(rows) < 2:
        await message.reply(
            "💫 <b>Not enough members yet</b>\n<blockquote>Let more people chat first ✨</blockquote>",
            parse_mode=H,
        )
        return

    a, b = random.sample(rows, 2)
    for r in (a, b):
        await services.ensure_user_and_wallet(pool, r["id"], r["username"], r["first_name"], bot_id=bid)

    gem = random.random() < 0.01
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock($1)", chat_id)
            last = await c.fetchval(
                "SELECT last_time FROM ax_ship_cooldowns WHERE bot_id=$1 AND chat_id=$2",
                bid,
                chat_id,
            )
            if last and (now - last).total_seconds() < 300:
                m, s = divmod(300 - int((now - last).total_seconds()), 60)
                cooldown_left = (m, s)
            else:
                cooldown_left = None
                for r in sorted((a, b), key=lambda row: row["id"]):
                    await services.award_xp(c, r["id"], 10, bot_id=bid)
                    if gem:
                        await c.execute(
                            "UPDATE wallets SET gems = gems + 1 WHERE bot_id=$1 AND user_id=$2",
                            bid,
                            r["id"],
                        )
                await c.execute(
                    "INSERT INTO ax_ship_cooldowns (bot_id, chat_id, last_time) VALUES ($1,$2,$3) "
                    "ON CONFLICT (chat_id) DO UPDATE SET bot_id=$1, last_time=$3",
                    bid,
                    chat_id,
                    now,
                )

    if cooldown_left is not None:
        m, s = cooldown_left
        await message.reply(
            f"💘 <b>Cupid is recharging</b>\n<blockquote>Try again in <b>{m:02d}m {s:02d}s</b> 💫</blockquote>",
            parse_mode=H,
        )
        return

    extra = "\n\n✨ <b>+10 XP</b> each" + ("\n💎 A rare gem appeared!" if gem else "")
    await send(
        message,
        "ship",
        card("ship", a["first_name"] or "Someone", b["first_name"] or "Someone", random.randint(0, 100), extra),
    )


@router.message(Command("match"))
async def match_cmd(message: Message, bot: Bot, bot_ctx: BotContext) -> None:
    if await groups_only(message):
        return
    me = message.from_user
    if me is None:
        return
    rp = message.reply_to_message
    if not rp or not rp.from_user:
        await message.reply(
            "💫 <b>Reply to someone</b>\n<blockquote>Use /match on their message ✨</blockquote>",
            parse_mode=H,
        )
        return
    t = rp.from_user
    bname = esc(get_brand(bot_ctx.bot_id).name)
    if t.id == me.id:
        await send(
            message,
            "match",
            "💖 <b>Self-love is important</b>\n<blockquote>No one understands you better than you 💫</blockquote>",
        )
    elif t.id == bot.id:
        await send(
            message,
            "match",
            f"🤖 <b>{bname} appreciates you</b>\n<blockquote>But we're just good friends 🌸</blockquote>",
        )
    else:
        day = datetime.now(IST).date().isoformat()
        await send(
            message,
            "match",
            card("match", me.first_name or "Someone", t.first_name or "Someone", score(me.id, t.id, day)),
        )


@router.message(Command("couple"))
async def couple_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if await groups_only(message):
        return
    bid = bot_ctx.bot_id
    chat_id = message.chat.id
    day = datetime.now(IST).date()
    rows = await members(pool, chat_id, bid)
    if len(rows) < 2:
        await message.reply(
            "💫 <b>Not enough members yet</b>\n<blockquote>Let more people chat first ✨</blockquote>",
            parse_mode=H,
        )
        return

    a, b = random.sample(rows, 2)
    for r in (a, b):
        await services.ensure_user_and_wallet(pool, r["id"], r["username"], r["first_name"], bot_id=bid)

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock($1)", chat_id)
            row = await c.fetchrow(
                "SELECT day, user_a, user_b, compatibility FROM ax_couple_of_day WHERE bot_id=$1 AND chat_id=$2",
                bid,
                chat_id,
            )
            fresh = not row or row["day"] != day
            if fresh:
                pct = score(a["id"], b["id"], day.isoformat())
                for r in sorted((a, b), key=lambda row_: row_["id"]):
                    w = await c.fetchrow(
                        "SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                        bid,
                        r["id"],
                    )
                    await c.execute(
                        "UPDATE wallets SET cash = cash + 500, updated_at = now() WHERE bot_id=$1 AND user_id=$2",
                        bid,
                        r["id"],
                    )
                    await services.insert_transaction(
                        c,
                        user_id=r["id"],
                        type_="COUPLE_REWARD",
                        currency="cash",
                        amount=500,
                        balance_before=w["cash"],
                        balance_after=w["cash"] + 500,
                        source="couple_command",
                        idempotency_key=f"couple:{bid}:{chat_id}:{day.isoformat()}:{r['id']}",
                        bot_id=bid,
                    )
                await c.execute(
                    "INSERT INTO ax_couple_of_day (bot_id, chat_id, day, user_a, user_b, compatibility) VALUES ($1,$2,$3,$4,$5,$6) "
                    "ON CONFLICT (chat_id) DO UPDATE SET bot_id=$1, day=$3, user_a=$4, user_b=$5, compatibility=$6",
                    bid,
                    chat_id,
                    day,
                    a["id"],
                    b["id"],
                    pct,
                )
                ids, pct_final = (a["id"], b["id"]), pct
            else:
                ids, pct_final = (row["user_a"], row["user_b"]), row["compatibility"]
            names = [
                await c.fetchval(
                    "SELECT first_name FROM users WHERE bot_id=$1 AND id=$2",
                    bid,
                    i,
                )
                or "Someone"
                for i in ids
            ]

    extra = (
        "\n\n💰 <b>$500</b> each · 🏅 Couple of the Day"
        if fresh
        else "\n\n🔁 <i>Today's couple is already chosen</i>"
    )
    await send(message, "couple", card("couple", names[0], names[1], pct_final, extra))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

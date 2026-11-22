"""Kill & Revive: Premium vs Normal payouts/XP, daily kill caps, and /revive (aiogram 3.x)."""
import random
from datetime import datetime, timedelta, timezone
import asyncpg
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
import config, services
from ax_adminpanel import OWNER_IDS
from ax_common import esc
from bot_context import BotContext

router = Router(name="ax_kill")
H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))

async def _dead_until(pool: asyncpg.Pool, user_id: int, bot_id: int) -> datetime | None:
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT dead_until FROM ax_kill_status WHERE bot_id=$1 AND user_id=$2", bot_id, user_id)
    if not row or row["dead_until"] <= datetime.now(timezone.utc):
        return None
    return row["dead_until"]

@router.message(Command("kill"))
async def kill_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if not user:
        return
    bid = bot_ctx.bot_id
    if await _dead_until(pool, user.id, bid):
        await message.reply("💀 You are dead right now. Use <code>/revive</code>.", parse_mode=H)
        return
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target or target.is_bot or target.id == user.id:
        await message.reply("Reply to another user with <code>/kill</code>.", parse_mode=H)
        return
    import ax_powers, ax_premium
    is_prem = (user.id in OWNER_IDS) or (await ax_premium.get_premium(pool, user.id, bot_id=bid) is not None)
    prem_row = await ax_premium.get_premium(pool, user.id, bot_id=bid)
    badge = ax_premium.player_badge_html(prem_row if is_prem else None)
    powers = await ax_powers.get_active_powers(pool, user.id, bid) if is_prem else {}
    bonus_cash = 150 if "reaper" in powers else 0
    daily_cap = (getattr(config, "PREMIUM_DAILY_KILL_LIMIT", 400) + (100 if "reaper" in powers else 0)) if is_prem else getattr(config, "NORMAL_DAILY_KILL_LIMIT", 200)
    c_min, c_max = (getattr(config, "PREMIUM_KILL_MIN", 200), getattr(config, "PREMIUM_KILL_MAX", 400)) if is_prem else (getattr(config, "NORMAL_KILL_MIN", 100), getattr(config, "NORMAL_KILL_MAX", 200))
    x_min, x_max = (getattr(config, "PREMIUM_KILL_XP_MIN", 10), getattr(config, "PREMIUM_KILL_XP_MAX", 20)) if is_prem else (getattr(config, "NORMAL_KILL_XP_MIN", 0), getattr(config, "NORMAL_KILL_XP_MAX", 10))
    amt = random.randint(c_min, c_max) + bonus_cash
    xp_g = random.randint(x_min, x_max)
    dead_until = datetime.now(timezone.utc) + timedelta(hours=random.uniform(7, 8))
    today = datetime.now(IST).date()
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    async with pool.acquire() as c:
        async with c.transaction():
            await ax_powers.ensure_tables(c)
            lim = await c.fetchrow("SELECT kills_used FROM ax_daily_limits WHERE bot_id=$1 AND user_id=$2 AND day_key=$3 FOR UPDATE", bid, user.id, today)
            used = int(lim["kills_used"]) if lim else 0
            if used >= daily_cap:
                await message.reply(f"⏳ Daily kill limit reached (<code>{used}/{daily_cap}</code>).", parse_mode=H)
                return
            await c.execute("INSERT INTO ax_kill_status (bot_id, user_id, dead_until) VALUES ($1,$2,$3) ON CONFLICT (user_id) DO UPDATE SET bot_id=EXCLUDED.bot_id, dead_until=EXCLUDED.dead_until", bid, target.id, dead_until)
            await c.execute("INSERT INTO ax_daily_limits (bot_id, user_id, day_key, kills_used) VALUES ($1,$2,$3,1) ON CONFLICT (bot_id, user_id, day_key) DO UPDATE SET kills_used = ax_daily_limits.kills_used + 1", bid, user.id, today)
            await c.execute("UPDATE wallets SET cash = cash + $1, updated_at=now() WHERE bot_id=$2 AND user_id=$3", amt, bid, user.id)
            if xp_g > 0:
                await services.award_xp(c, user.id, xp_g, bot_id=bid)
    try:
        import ax_tasks
        await ax_tasks.record_task_action(pool, user.id, "kills", bot_id=bid)
    except Exception:
        pass
    await message.reply(f"{badge} <b>{esc(user.first_name or 'Player')}</b> killed <b>{esc(target.first_name or 'Target')}</b>!\n<blockquote>💰 Earned: <code>+${amt:,}</code> • ✨ <code>+{xp_g} XP</code></blockquote>", parse_mode=H)

@router.message(Command("revive"))
async def revive_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if not user:
        return
    bid = bot_ctx.bot_id
    target = user
    if message.reply_to_message and message.reply_to_message.from_user and not message.reply_to_message.from_user.is_bot:
        target = message.reply_to_message.from_user
    if not await _dead_until(pool, target.id, bid):
        await message.reply("✨ Already alive!")
        return
    cost = random.randint(300, 400)
    async with pool.acquire() as c:
        async with c.transaction():
            w = await c.fetchrow("SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, user.id)
            if not w or int(w["cash"]) < cost:
                await message.reply(f"💀 Reviving costs <code>${cost}</code>.", parse_mode=H)
                return
            await c.execute("DELETE FROM ax_kill_status WHERE bot_id=$1 AND user_id=$2", bid, target.id)
            await c.execute("UPDATE wallets SET cash = cash - $1, updated_at=now() WHERE bot_id=$2 AND user_id=$3", cost, bid, user.id)
    await message.reply(f"✨ Revived! Paid <code>${cost}</code>.", parse_mode=H)

def register(dp):
    if router.parent_router is None:
        dp.include_router(router)

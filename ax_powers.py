"""Premium-only Powers system: /powers, /pinfo, /act, /mypowers (/mp), /ph (aiogram 3.x)."""
import html
import logging
from datetime import datetime, timedelta, timezone
import asyncpg
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
import bot_context, config, services
from ax_adminpanel import OWNER_IDS
from ax_common import cmd_args
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="ax_powers")
H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))

POWERS: dict[str, dict] = {
    "heist": {
        "emoji": "🐺",
        "name": "Shadow Heist",
        "cost_per_day": 5,
        "summary": "Raises single /rob limit from $30,000 up to $100,000 (1 Lakh)!",
        "details": "• Single /rob cap increases from $30,000 → <b>$100,000</b>\n• Adds +100 extra daily /rob attempts\n• Stacks with other active powers",
    },
    "reaper": {
        "emoji": "⚔️",
        "name": "Crimson Reaper",
        "cost_per_day": 4,
        "summary": "Boosts /kill earnings (+$150 bonus) and adds +100 daily kills.",
        "details": "• Adds <b>+$150</b> bonus cash on every successful /kill\n• Adds +100 extra daily /kill attempts\n• Stacks with other active powers",
    },
    "fortress": {
        "emoji": "🛡️",
        "name": "Iron Fortress",
        "cost_per_day": 4,
        "summary": "Increases robbery wallet protection from 30% up to 50%.",
        "details": "• Increases robbery-safe wallet buffer from <b>30% → 50%</b>\n• Reduces failed-rob penalty by 25%\n• Stacks with other active powers",
    },
}

ALIASES = {
    "heist": "heist", "shadow": "heist", "shadowheist": "heist", "rob": "heist",
    "reaper": "reaper", "crimson": "reaper", "crimsonreaper": "reaper", "kill": "reaper",
    "fortress": "fortress", "iron": "fortress", "ironfortress": "fortress", "shield": "fortress",
}

def resolve_power_key(raw: str) -> str | None:
    return ALIASES.get((raw or "").strip().lower().replace("_", "").replace("-", ""))

async def ensure_tables(conn) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS ax_user_powers ("
        "bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, power_key TEXT NOT NULL, "
        "expires_at TIMESTAMPTZ NOT NULL, activated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (bot_id, user_id, power_key))"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS ax_daily_limits ("
        "bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, day_key DATE NOT NULL, "
        "kills_used INT NOT NULL DEFAULT 0, robs_used INT NOT NULL DEFAULT 0, gems_used INT NOT NULL DEFAULT 0, "
        "PRIMARY KEY (bot_id, user_id, day_key))"
    )

async def get_active_powers(pool_or_conn, user_id: int, bot_id: int) -> dict[str, datetime]:
    now = datetime.now(timezone.utc)
    sql = "SELECT power_key, expires_at FROM ax_user_powers WHERE bot_id=$1 AND user_id=$2 AND expires_at > $3"
    if hasattr(pool_or_conn, "acquire"):
        async with pool_or_conn.acquire() as conn:
            await ensure_tables(conn)
            rows = await conn.fetch(sql, bot_id, user_id, now)
    else:
        await ensure_tables(pool_or_conn)
        rows = await pool_or_conn.fetch(sql, bot_id, user_id, now)
    return {r["power_key"]: r["expires_at"] for r in rows}

async def consume_daily_gems(conn, user_id: int, gems_amount: int, bot_id: int) -> tuple[bool, int, int]:
    await ensure_tables(conn)
    cap = getattr(config, "DAILY_GEM_USE_LIMIT", 50)
    today = datetime.now(IST).date()
    row = await conn.fetchrow("SELECT gems_used FROM ax_daily_limits WHERE bot_id=$1 AND user_id=$2 AND day_key=$3 FOR UPDATE", bot_id, user_id, today)
    used = int(row["gems_used"]) if row else 0
    if used + gems_amount > cap:
        return False, used, cap
    await conn.execute(
        "INSERT INTO ax_daily_limits (bot_id, user_id, day_key, gems_used) VALUES ($1,$2,$3,$4) "
        "ON CONFLICT (bot_id, user_id, day_key) DO UPDATE SET gems_used = ax_daily_limits.gems_used + EXCLUDED.gems_used",
        bot_id, user_id, today, gems_amount,
    )
    return True, used + gems_amount, cap

def _fmt_left(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    parts = [p for p in (f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m" if m else "") if p]
    return " ".join(parts) or "0m"

async def _is_premium(pool: asyncpg.Pool, uid: int, bid: int) -> bool:
    if uid in OWNER_IDS:
        return True
    import ax_premium
    return (await ax_premium.get_premium(pool, uid, bot_id=bid)) is not None

@router.message(Command("powers"))
async def powers_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    bname = html.escape(get_brand(bot_ctx.bot_id).name)
    lines = [f"{p['emoji']} <b>{p['name']}</b> (<code>{k}</code>) — <code>{p['cost_per_day']} 💎/day</code>\n      <i>{p['summary']}</i>" for k, p in POWERS.items()]
    await message.reply(f"🐺 <b>{bname} SPECIAL POWERS</b>\n<blockquote>{chr(10).join(lines)}</blockquote>\nUse <code>/act &lt;power&gt; &lt;days&gt;</code>, <code>/mypowers</code>, or <code>/ph</code>.", parse_mode=H)

@router.message(Command("pinfo"))
async def pinfo_cmd(message: Message) -> None:
    args = cmd_args(message)
    key = resolve_power_key(args[0]) if args else None
    if not key:
        await message.reply("Use: <code>/pinfo heist</code>, <code>/pinfo reaper</code>, or <code>/pinfo fortress</code>", parse_mode=H)
        return
    p = POWERS[key]
    await message.reply(f"{p['emoji']} <b>{p['name']}</b>\n<blockquote>{p['details']}</blockquote>", parse_mode=H)

@router.message(Command("act"))
async def act_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    if not await _is_premium(pool, user.id, bid):
        await message.reply("💓 Powers are for <b>Premium users only</b>. Use <code>/premium</code>.", parse_mode=H)
        return
    args = cmd_args(message)
    if len(args) < 2 or not args[1].isdigit() or int(args[1]) <= 0:
        await message.reply("Use: <code>/act &lt;power&gt; &lt;days&gt;</code> (example: <code>/act heist 2</code>)", parse_mode=H)
        return
    key = resolve_power_key(args[0])
    if not key:
        await message.reply("Unknown power. Choose <code>heist</code>, <code>reaper</code>, or <code>fortress</code>.", parse_mode=H)
        return
    days = min(30, int(args[1]))
    power = POWERS[key]
    total_gems = power["cost_per_day"] * days
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_tables(conn)
            w = await conn.fetchrow("SELECT gems FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, user.id)
            have_gems = int(w["gems"]) if w else 0
            if have_gems < total_gems:
                await message.reply(f"💎 Need <code>{total_gems}</code> Gems (you have <code>{have_gems}</code>).", parse_mode=H)
                return
            ok_cap, used_today, daily_cap = await consume_daily_gems(conn, user.id, total_gems, bid)
            if not ok_cap:
                await message.reply(f"⚠️ Daily Gem limit reached (<code>{used_today}/{daily_cap}</code>).", parse_mode=H)
                return
            now = datetime.now(timezone.utc)
            ex = await conn.fetchrow("SELECT expires_at FROM ax_user_powers WHERE bot_id=$1 AND user_id=$2 AND power_key=$3 FOR UPDATE", bid, user.id, key)
            base_t = ex["expires_at"] if (ex and ex["expires_at"] and ex["expires_at"] > now) else now
            new_exp = base_t + timedelta(days=days)
            after_gems = have_gems - total_gems
            await conn.execute("UPDATE wallets SET gems=$1, updated_at=now() WHERE bot_id=$2 AND user_id=$3", after_gems, bid, user.id)
            await conn.execute("INSERT INTO ax_user_powers (bot_id, user_id, power_key, expires_at, activated_at) VALUES ($1,$2,$3,$4,now()) ON CONFLICT (bot_id, user_id, power_key) DO UPDATE SET expires_at=EXCLUDED.expires_at, activated_at=now()", bid, user.id, key, new_exp)
    await message.reply(f"{power['emoji']} Activated <b>{power['name']}</b> for <code>{days}d</code>! Gems left: <code>{after_gems}</code>.", parse_mode=H)

@router.message(Command("mypowers", "mp"))
async def mypowers_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if not user:
        return
    if not await _is_premium(pool, user.id, bot_ctx.bot_id):
        await message.reply("💓 Powers are for <b>Premium users only</b>.", parse_mode=H)
        return
    active = await get_active_powers(pool, user.id, bot_ctx.bot_id)
    if not active:
        await message.reply("No active powers right now. Use <code>/powers</code>.", parse_mode=H)
        return
    lines = [f"{POWERS[k]['emoji']} <b>{POWERS[k]['name']}</b> — <code>{_fmt_left(exp)}</code>" for k, exp in active.items() if k in POWERS]
    await message.reply(f"🐺 <b>YOUR ACTIVE POWERS</b>\n<blockquote>{chr(10).join(lines)}</blockquote>", parse_mode=H)

@router.message(Command("ph"))
async def ph_cmd(message: Message) -> None:
    await message.reply("🐺 <b>POWERS HELP</b>\n<blockquote>/powers • /pinfo &lt;power&gt; • /act &lt;power&gt; &lt;days&gt; • /mypowers (/mp)</blockquote>", parse_mode=H)

def register(dp):
    if router.parent_router is None:
        dp.include_router(router)

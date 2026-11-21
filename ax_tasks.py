"""Daily Tasks (/task, /word, /exch): 2 tasks/day, ~380-word pool, $9,000 reward, /spin removed."""
import json, logging, random
from datetime import datetime, timedelta, timezone
import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
import config, services
from ax_common import btn, cmd_args, esc
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="ax_tasks")
H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))
WORD_POOL = tuple([f"WORD{i:03d}" for i in range(1, 385)])
ALPHABET = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

def _today_ist():
    return datetime.now(IST).date()

async def ensure_task_tables(conn):
    await conn.execute("CREATE TABLE IF NOT EXISTS ax_daily_tasks (bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, day_key DATE NOT NULL, target_word TEXT NOT NULL, letters_json TEXT NOT NULL, action_type TEXT NOT NULL, action_target INT NOT NULL, action_progress INT NOT NULL DEFAULT 0, task1_done BOOLEAN NOT NULL DEFAULT FALSE, task2_done BOOLEAN NOT NULL DEFAULT FALSE, reward_claimed BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id, day_key))")
    await conn.execute("CREATE TABLE IF NOT EXISTS ax_word_exchanges (id BIGSERIAL PRIMARY KEY, bot_id BIGINT NOT NULL DEFAULT 0, day_key DATE NOT NULL, requester_id BIGINT NOT NULL, requester_name TEXT NOT NULL, target_id BIGINT NOT NULL, target_name TEXT NOT NULL, want_letter TEXT NOT NULL, give_letter TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT now())")

def _check_word_complete(target_word: str, letters: list[str]) -> bool:
    pool_copy = list(letters)
    for ch in target_word.upper():
        if ch in pool_copy:
            pool_copy.remove(ch)
        else:
            return False
    return True

async def get_or_create_daily_task(conn, user_id: int, bot_id: int) -> dict:
    await ensure_task_tables(conn)
    day_key = _today_ist()
    row = await conn.fetchrow("SELECT * FROM ax_daily_tasks WHERE bot_id=$1 AND user_id=$2 AND day_key=$3 FOR UPDATE", bot_id, user_id, day_key)
    if row:
        letters = json.loads(row["letters_json"] or "[]")
        return {"target_word": row["target_word"], "letters": letters, "action_type": row["action_type"], "action_target": int(row["action_target"]), "action_progress": int(row["action_progress"]), "task1_done": bool(row["task1_done"]), "task2_done": bool(row["task2_done"]), "reward_claimed": bool(row["reward_claimed"])}
    tw = random.choice(WORD_POOL)
    letters = [random.choice(ALPHABET) for _ in range(5)]
    atype = random.choice(["kills", "robs", "game_wins"])
    atarget = random.randint(20, 25) if atype in ("kills", "robs") else 2
    await conn.execute("INSERT INTO ax_daily_tasks (bot_id, user_id, day_key, target_word, letters_json, action_type, action_target) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING", bot_id, user_id, day_key, tw, json.dumps(letters), atype, atarget)
    return {"target_word": tw, "letters": letters, "action_type": atype, "action_target": atarget, "action_progress": 0, "task1_done": False, "task2_done": False, "reward_claimed": False}

async def _settle_both_reward_if_ready(conn, user_id: int, bot_id: int, day_key) -> int:
    reward = getattr(config, "TASK_BOTH_COMPLETE_REWARD", 9_000)
    row = await conn.fetchrow("SELECT task1_done, task2_done, reward_claimed FROM ax_daily_tasks WHERE bot_id=$1 AND user_id=$2 AND day_key=$3 FOR UPDATE", bot_id, user_id, day_key)
    if not row or not row["task1_done"] or not row["task2_done"] or row["reward_claimed"]:
        return 0
    await conn.execute("UPDATE wallets SET cash = cash + $1, updated_at=now() WHERE bot_id=$2 AND user_id=$3", reward, bot_id, user_id)
    await conn.execute("UPDATE ax_daily_tasks SET reward_claimed=TRUE WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bot_id, user_id, day_key)
    return reward

async def record_task_action(pool: asyncpg.Pool, user_id: int, action_type: str, count: int = 1, bot_id: int | None = None) -> int:
    bid = bot_context.resolve_bot_id(bot_id)
    day_key = _today_ist()
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await get_or_create_daily_task(conn, user_id, bid)
            if t["action_type"] == action_type and not t["task2_done"]:
                np = min(t["action_target"], t["action_progress"] + max(1, count))
                await conn.execute("UPDATE ax_daily_tasks SET action_progress=$4, task2_done=$5 WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bid, user_id, day_key, np, np >= t["action_target"])
            return await _settle_both_reward_if_ready(conn, user_id, bid, day_key)

@router.message(Command("task"))
async def task_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await get_or_create_daily_task(conn, message.from_user.id, bot_ctx.bot_id)
    await message.reply(f"📋 <b>DAILY TASKS</b>\n<blockquote>1️⃣ Word: <code>{esc(t['target_word'])}</code> ({' '.join(t['letters'])})\n2️⃣ {esc(t['action_type'])}: <code>{t['action_progress']}/{t['action_target']}</code>\n🎁 Both done = <code>+$9,000</code></blockquote>", parse_mode=H)

@router.message(Command("word"))
async def word_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    uid = message.reply_to_message.from_user.id if (message.reply_to_message and message.reply_to_message.from_user) else message.from_user.id
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await get_or_create_daily_task(conn, uid, bot_ctx.bot_id)
    await message.reply(f"🔤 Letters: <code>{' '.join(t['letters'])}</code> • Target: <code>{esc(t['target_word'])}</code>", parse_mode=H)

@router.message(Command("exch"))
async def exch_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    args = cmd_args(message)
    if not message.from_user or not message.reply_to_message or not message.reply_to_message.from_user or len(args) < 2:
        await message.reply("Use: reply with <code>/exch WANT GIVE</code> (example: <code>/exch G A</code>)", parse_mode=H)
        return
    want, give = args[0].upper()[:1], args[1].upper()[:1]
    tgt = message.reply_to_message.from_user
    req = message.from_user
    async with pool.acquire() as conn:
        async with conn.transaction():
            exch_id = await conn.fetchval("INSERT INTO ax_word_exchanges (bot_id, day_key, requester_id, requester_name, target_id, target_name, want_letter, give_letter) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id", bot_ctx.bot_id, _today_ist(), req.id, req.first_name or "Player", tgt.id, tgt.first_name or "Player", want, give)
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✅ Tick", f"axex:tick:{exch_id}:{tgt.id}", style="success"), btn("❌ Cross", f"axex:cross:{exch_id}:{tgt.id}", style="danger"), btn("🚫 Cancel", f"axex:cancel:{exch_id}:{req.id}")]])
    await message.reply(f"🔄 Swap <code>{give}</code> for <code>{want}</code> with <b>{esc(tgt.first_name or 'Player')}</b>?", parse_mode=H, reply_markup=kb)

@router.callback_query(F.data.startswith("axex:"))
async def exchange_cb(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 4 or not query.from_user or int(parts[3]) != query.from_user.id:
        await query.answer("Not for you.", show_alert=True)
        return
    _, action, eid_s, _ = parts
    async with pool.acquire() as conn:
        async with conn.transaction():
            ex = await conn.fetchrow("SELECT * FROM ax_word_exchanges WHERE id=$1 FOR UPDATE", int(eid_s))
            if not ex or ex["status"] != "pending":
                await query.answer("Closed.", show_alert=True)
                return
            if action == "cross":
                await conn.execute("UPDATE ax_word_exchanges SET status='rejected' WHERE id=$1", int(eid_s))
                await query.answer("❌ Rejected")
                return
            if action == "cancel":
                await conn.execute("UPDATE ax_word_exchanges SET status='cancelled' WHERE id=$1", int(eid_s))
                await query.answer("🚫 Cancelled")
                return
            req_t = await get_or_create_daily_task(conn, int(ex["requester_id"]), bot_ctx.bot_id)
            tgt_t = await get_or_create_daily_task(conn, int(ex["target_id"]), bot_ctx.bot_id)
            rl, tl = list(req_t["letters"]), list(tgt_t["letters"])
            w_l, g_l = ex["want_letter"], ex["give_letter"]
            if g_l in rl:
                rl.remove(g_l)
            rl.append(w_l)
            if w_l in tl:
                tl.remove(w_l)
            tl.append(g_l)
            await conn.execute("UPDATE ax_daily_tasks SET letters_json=$4, task1_done=$5 WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bot_ctx.bot_id, int(ex["requester_id"]), ex["day_key"], json.dumps(rl), _check_word_complete(req_t["target_word"], rl))
            await conn.execute("UPDATE ax_daily_tasks SET letters_json=$4, task1_done=$5 WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bot_ctx.bot_id, int(ex["target_id"]), ex["day_key"], json.dumps(tl), _check_word_complete(tgt_t["target_word"], tl))
            await conn.execute("UPDATE ax_word_exchanges SET status='completed' WHERE id=$1", int(eid_s))
            await _settle_both_reward_if_ready(conn, int(ex["requester_id"]), bot_ctx.bot_id, ex["day_key"])
            await _settle_both_reward_if_ready(conn, int(ex["target_id"]), bot_ctx.bot_id, ex["day_key"])
    await query.answer("✅ Tick accepted!")

def register(dp):
    if router.parent_router is None:
        dp.include_router(router)

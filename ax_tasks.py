"""Daily Tasks (/task, /word, /exch): 2 tasks a day, one reward when both are done.

Task 1 (word): your 5 letters have ONE wrong letter. Swap it with another player (/exch) to spell the word.
Task 2 (action): do 20-25 kills, 20-25 robs, or win 2 games (cash games and Bluff count).
Both done = $9,000 (paid once a day). Resets at midnight IST.
"""
import json
import logging
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import bot_context
import config
import services
from ax_common import btn, cmd_args, esc
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_tasks")
H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))
ALPHABET = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
EXCH_LIFETIME = timedelta(minutes=10)
REWARD = int(getattr(config, "TASK_BOTH_COMPLETE_REWARD", 9_000))
ACTIONS = {"kills": "Kill players", "robs": "Rob players", "game_wins": "Win games"}

WORD_POOL = tuple("""
    ACTOR ADMIT ADOPT AGENT ALARM ALONE ALOUD AMBER ANGEL ANGER APPLE ARROW ASIDE AZURE BACON BADGE
    BASIC BEACH BEAST BELOW BENCH BLAME BLAZE BLEND BLISS BLOOM BRAVE BREAD BRICK CABIN CANDY CHARM
    CHASE CHIEF CIVIC CLEAN CLIMB CLOCK CLOUD CORAL CRAFT CRISP CROWN CYCLE DAISY DANCE DEALT DEBUT
    DELTA DEPTH DINER DODGE DOZEN DRAIN DREAM DRESS DRIFT DRIVE DWARF EAGLE EARTH ELBOW ELDER ELITE
    EMBER ENJOY ENTRY EQUAL EQUIP ETHIC EVENT EXACT EXTRA FABLE FAIRY FEVER FIBER FIELD FINAL FLAIR
    FLAME FLOCK FLOUR FLUTE FORGE FRONT FROST FRUIT GAMER GHOST GIANT GLAZE GLIDE GLOBE GLORY GLOVE
    GRACE GRAIN GRAND GRAPE GRASS GUEST GUIDE HABIT HAPPY HARSH HASTE HATCH HEART HEAVY HELLO HOBBY
    HONEY HORSE HOTEL HOUSE HOVER HUMOR ICING IDEAL IDIOM IGLOO IMAGE INBOX INDEX INFER INLET INNER
    INPUT IRONY ISSUE IVORY JAUNT JAZZY JEANS JELLY JEWEL JIFFY JOINT JOKER JOLLY JOUST JUDGE JUICE
    JUMBO KARMA KAYAK KEBAB KHAKI KINGS KIOSK KITTY KIWIS KNACK KNEEL KNIFE KNOCK KNOWN KOALA KUDOS
    LASER LAUGH LEAPT LEMON LEVER LIGHT LILAC LIMIT LLAMA LODGE LOFTY LOYAL LUCID LUCKY LUNAR MAGIC
    MANGO MAPLE MARCH MEDAL MERCY MINOR MIRTH MOCHA MONEY MORAL MOTOR MOUNT MOUSE MUSIC NACHO NASTY
    NAVAL NERVE NEVER NEXUS NICER NIGHT NINTH NOBLE NOISE NORTH NOVEL NURSE NYLON OASIS OCCUR OCEAN
    ODDLY OFFER OILED OLIVE ONION ONSET OPERA ORBIT OTHER OUGHT OWNER OXIDE PAPER PASTA PEACE PEARL
    PHONE PIANO PILOT PLAIN PLANT PLUME PRESS PRIDE PRIME PRISM PRIZE QUACK QUAIL QUART QUASI QUEEN
    QUEST QUICK QUIET QUILL QUILT QUIRK QUOTA QUOTE RADAR RANCH RAPID RATIO REALM REIGN RIDGE RINSE
    RIVAL RIVER ROBIN ROBOT ROUGH RUSTY SCARF SCENE SCOUT SHEEP SHIFT SHINE SIREN SIXTH SLEEK SOLAR
    SPICE STONE STORM SUGAR SWIFT TABLE TEMPO THIEF THORN THUMB TIGER TOKEN TOWER TRAIL TRAIN TREAD
    TROUT TRUST TULIP TWEAK ULTRA UMBRA UNCLE UNDER UNFIT UNION UNITE UNITY UNMET UNZIP UPPER UPSET
    URBAN USHER USUAL VALOR VALUE VAPOR VAULT VERSE VIGIL VIGOR VIRAL VISIT VITAL VIVID VIXEN VODKA
    VOICE VOWEL WACKY WAGON WALTZ WATER WEDGE WHALE WHEAT WHEEL WIDER WIDOW WOMAN WOMEN WORLD WORRY
    WRIST XENON XYLEM YACHT YARDS YEARN YEAST YELLS YIELD YODEL YOLKS YOUNG YOUTH YUMMY ZAPPY ZEBRA
    ZEROS ZESTY ZILCH ZINGY ZIPPY ZONAL ZONED ZONES
""".split())


def _today_ist():
    return datetime.now(IST).date()


async def ensure_task_tables(conn):
    await conn.execute("CREATE TABLE IF NOT EXISTS ax_daily_tasks (bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, day_key DATE NOT NULL, target_word TEXT NOT NULL, letters_json TEXT NOT NULL, action_type TEXT NOT NULL, action_target INT NOT NULL, action_progress INT NOT NULL DEFAULT 0, task1_done BOOLEAN NOT NULL DEFAULT FALSE, task2_done BOOLEAN NOT NULL DEFAULT FALSE, reward_claimed BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id, day_key))")
    await conn.execute("CREATE TABLE IF NOT EXISTS ax_word_exchanges (id BIGSERIAL PRIMARY KEY, bot_id BIGINT NOT NULL DEFAULT 0, day_key DATE NOT NULL, requester_id BIGINT NOT NULL, requester_name TEXT NOT NULL, target_id BIGINT NOT NULL, target_name TEXT NOT NULL, want_letter TEXT NOT NULL, give_letter TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT now())")


def _check_word_complete(target_word: str, letters: list[str]) -> bool:
    return Counter(target_word.upper()) == Counter(x.upper() for x in letters)


def _make_letters(word: str) -> list[str]:
    """The word's letters with exactly one replaced by a letter the word does not use, then shuffled."""
    letters = list(word.upper())
    wrong = random.choice([c for c in ALPHABET if c not in letters])
    letters[random.randrange(len(letters))] = wrong
    random.shuffle(letters)
    return letters


def _new_task() -> tuple[str, list[str], str, int]:
    word = random.choice(WORD_POOL)
    atype = random.choice(["kills", "robs", "game_wins"])
    lo = int(getattr(config, "TASK_ACTION_TARGET_MIN", 20))
    hi = int(getattr(config, "TASK_ACTION_TARGET_MAX", 25))
    atarget = random.randint(lo, hi) if atype in ("kills", "robs") else 2
    return word, _make_letters(word), atype, atarget


def _row_to_task(row) -> dict:
    return {
        "target_word": row["target_word"], "letters": json.loads(row["letters_json"] or "[]"),
        "action_type": row["action_type"], "action_target": int(row["action_target"]),
        "action_progress": int(row["action_progress"]), "task1_done": bool(row["task1_done"]),
        "task2_done": bool(row["task2_done"]), "reward_claimed": bool(row["reward_claimed"]),
    }


async def get_or_create_daily_task(conn, user_id: int, bot_id: int) -> dict:
    await ensure_task_tables(conn)
    day_key = _today_ist()
    row = await conn.fetchrow("SELECT * FROM ax_daily_tasks WHERE bot_id=$1 AND user_id=$2 AND day_key=$3 FOR UPDATE", bot_id, user_id, day_key)
    if row and row["target_word"].startswith("WORD"):
        # old placeholder task from before the fix: give a real one (keeps action progress)
        word = random.choice(WORD_POOL)
        letters = _make_letters(word)
        await conn.execute("UPDATE ax_daily_tasks SET target_word=$4, letters_json=$5, task1_done=FALSE WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bot_id, user_id, day_key, word, json.dumps(letters))
        row = await conn.fetchrow("SELECT * FROM ax_daily_tasks WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bot_id, user_id, day_key)
    if row:
        return _row_to_task(row)
    word, letters, atype, atarget = _new_task()
    await conn.execute("INSERT INTO ax_daily_tasks (bot_id, user_id, day_key, target_word, letters_json, action_type, action_target) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING", bot_id, user_id, day_key, word, json.dumps(letters), atype, atarget)
    return {"target_word": word, "letters": letters, "action_type": atype, "action_target": atarget, "action_progress": 0, "task1_done": False, "task2_done": False, "reward_claimed": False}


async def _settle_both_reward_if_ready(conn, user_id: int, bot_id: int, day_key) -> int:
    row = await conn.fetchrow("SELECT task1_done, task2_done, reward_claimed FROM ax_daily_tasks WHERE bot_id=$1 AND user_id=$2 AND day_key=$3 FOR UPDATE", bot_id, user_id, day_key)
    if not row or not row["task1_done"] or not row["task2_done"] or row["reward_claimed"]:
        return 0
    w = await conn.fetchrow("SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bot_id, user_id)
    if w is None:
        return 0  # no wallet yet: try again on the next action
    await conn.execute("UPDATE wallets SET cash = cash + $1, updated_at=now() WHERE bot_id=$2 AND user_id=$3", REWARD, bot_id, user_id)
    await services.insert_transaction(
        conn, user_id=user_id, type_="TASK_REWARD", currency="cash", amount=REWARD,
        balance_before=int(w["cash"]), balance_after=int(w["cash"]) + REWARD, source="daily_tasks",
        metadata={"day": str(day_key)}, idempotency_key=f"task:{bot_id}:{user_id}:{day_key}", bot_id=bot_id,
    )
    await conn.execute("UPDATE ax_daily_tasks SET reward_claimed=TRUE, updated_at=now() WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bot_id, user_id, day_key)
    return REWARD


async def record_task_action(pool: asyncpg.Pool, user_id: int, action_type: str, count: int = 1, bot_id: int | None = None) -> int:
    """Count one kill / rob / game win for today's task. Returns the reward paid (0 if none)."""
    bid = bot_context.resolve_bot_id(bot_id)
    day_key = _today_ist()
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await get_or_create_daily_task(conn, user_id, bid)
            if t["action_type"] == action_type and not t["task2_done"]:
                np_ = min(t["action_target"], t["action_progress"] + max(1, count))
                await conn.execute("UPDATE ax_daily_tasks SET action_progress=$4, task2_done=$5, updated_at=now() WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bid, user_id, day_key, np_, np_ >= t["action_target"])
            return await _settle_both_reward_if_ready(conn, user_id, bid, day_key)


def _bar(done: int, total: int, width: int = 10) -> str:
    n = int(width * min(done, total) / total) if total else width
    return "█" * n + "░" * (width - n)


def _task_text(t: dict) -> str:
    word_line = "✅ Done!" if t["task1_done"] else f"Make the word <code>{esc(t['target_word'])}</code>"
    act = ACTIONS.get(t["action_type"], t["action_type"])
    prog = f"{t['action_progress']}/{t['action_target']}"
    status = "✅ Done!" if t["task2_done"] else f"<code>{_bar(t['action_progress'], t['action_target'])}</code> {prog}"
    if t["reward_claimed"]:
        foot = f"🎁 Reward <b>${REWARD:,}</b> was paid today. Come back tomorrow!"
    else:
        foot = f"🎁 Finish both to win <b>${REWARD:,}</b>"
    return (
        "📋 <b>DAILY TASKS</b>\n"
        f"<blockquote>1️⃣ <b>Word task</b>\n{word_line}\n"
        f"Your letters: <code>{' '.join(t['letters'])}</code>\n"
        "One of them is wrong. Swap it with a friend using /exch.\n\n"
        f"2️⃣ <b>{act}</b>\n{status}</blockquote>\n"
        f"{foot}\n<i>Resets at midnight IST.</i>"
    )


@router.message(Command("task", "tasks"))
async def task_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await get_or_create_daily_task(conn, message.from_user.id, bot_ctx.bot_id)
    await message.reply(_task_text(t), parse_mode=H)


@router.message(Command("word"))
async def word_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    other = message.reply_to_message.from_user if (message.reply_to_message and message.reply_to_message.from_user) else None
    if other and other.id != message.from_user.id and not other.is_bot:
        async with pool.acquire() as conn:
            async with conn.transaction():
                t = await get_or_create_daily_task(conn, other.id, bot_ctx.bot_id)
        await message.reply(f"🔤 <b>{esc(other.first_name or 'Player')}</b> has: <code>{' '.join(t['letters'])}</code>\n"
                            "To swap a letter: reply to them with <code>/exch WANT GIVE</code>", parse_mode=H)
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await get_or_create_daily_task(conn, message.from_user.id, bot_ctx.bot_id)
    if t["task1_done"]:
        await message.reply("✅ Your word is done for today!", parse_mode=H)
        return
    await message.reply(f"🔤 Your letters: <code>{' '.join(t['letters'])}</code>\n"
                        f"🎯 Make: <code>{esc(t['target_word'])}</code>\n"
                        "One letter is wrong. Reply to a friend with <code>/exch WANT GIVE</code>\n"
                        "Example: <code>/exch G A</code> asks for their G and gives your A.", parse_mode=H)


@router.message(Command("exch"))
async def exch_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    args = cmd_args(message)
    rep = message.reply_to_message
    if not message.from_user or not rep or not rep.from_user or len(args) < 2:
        await message.reply("Use: reply to a friend with <code>/exch WANT GIVE</code>\nExample: <code>/exch G A</code> (you get their G, they get your A)", parse_mode=H)
        return
    want, give = args[0].upper()[:1], args[1].upper()[:1]
    tgt, req = rep.from_user, message.from_user
    if tgt.id == req.id or tgt.is_bot:
        await message.reply("Reply to another player (not yourself or a bot).")
        return
    if want not in ALPHABET or give not in ALPHABET or want == give:
        await message.reply("Use two different letters, like <code>/exch G A</code>.", parse_mode=H)
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            rt = await get_or_create_daily_task(conn, req.id, bot_ctx.bot_id)
            tt = await get_or_create_daily_task(conn, tgt.id, bot_ctx.bot_id)
            if rt["task1_done"]:
                await message.reply("✅ Your word is already done today.")
                return
            if tt["task1_done"]:
                await message.reply("That player already finished their word today.")
                return
            if give not in rt["letters"]:
                await message.reply(f"You don't have the letter <code>{give}</code>. Yours: <code>{' '.join(rt['letters'])}</code>", parse_mode=H)
                return
            if want not in tt["letters"]:
                await message.reply(f"They don't have the letter <code>{want}</code>. Theirs: <code>{' '.join(tt['letters'])}</code>", parse_mode=H)
                return
            exch_id = await conn.fetchval("INSERT INTO ax_word_exchanges (bot_id, day_key, requester_id, requester_name, target_id, target_name, want_letter, give_letter) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id", bot_ctx.bot_id, _today_ist(), req.id, req.first_name or "Player", tgt.id, tgt.first_name or "Player", want, give)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        btn("✅ Accept", f"axex:tick:{exch_id}:{tgt.id}", style="success"),
        btn("❌ Decline", f"axex:cross:{exch_id}:{tgt.id}", style="danger"),
        btn("🚫 Cancel", f"axex:cancel:{exch_id}:{req.id}"),
    ]])
    await message.reply(
        f"🔄 <b>{esc(req.first_name or 'Player')}</b> offers <code>{give}</code> for <code>{want}</code>.\n"
        f"<b>{esc(tgt.first_name or 'Player')}</b>, do you accept? <i>(10 minutes)</i>", parse_mode=H, reply_markup=kb)


async def _finish_msg(query: CallbackQuery, text: str) -> None:
    try:
        if query.message is not None:
            await query.message.edit_text(text, parse_mode=H)
    except Exception:
        pass


@router.callback_query(F.data.startswith("axex:"))
async def exchange_cb(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 4 or not query.from_user or int(parts[3]) != query.from_user.id:
        await query.answer("This button is not for you.", show_alert=True)
        return
    _, action, eid_s, _ = parts
    bid = bot_ctx.bot_id
    msg = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            ex = await conn.fetchrow("SELECT * FROM ax_word_exchanges WHERE id=$1 AND bot_id=$2 FOR UPDATE", int(eid_s), bid)
            if not ex or ex["status"] != "pending":
                await query.answer("This swap is closed.", show_alert=True)
                return
            if action in ("cross", "cancel"):
                await conn.execute("UPDATE ax_word_exchanges SET status=$2 WHERE id=$1", int(eid_s), "rejected" if action == "cross" else "cancelled")
                msg = "❌ Swap declined." if action == "cross" else "🚫 Swap cancelled."
            elif datetime.now(timezone.utc) - ex["created_at"] > EXCH_LIFETIME or ex["day_key"] != _today_ist():
                await conn.execute("UPDATE ax_word_exchanges SET status='expired' WHERE id=$1", int(eid_s))
                msg = "⌛ This swap expired."
            else:
                rid, tid, w_l, g_l = int(ex["requester_id"]), int(ex["target_id"]), ex["want_letter"], ex["give_letter"]
                rt = await get_or_create_daily_task(conn, rid, bid)
                tt = await get_or_create_daily_task(conn, tid, bid)
                rl, tl = list(rt["letters"]), list(tt["letters"])
                if rt["task1_done"] or tt["task1_done"] or g_l not in rl or w_l not in tl:
                    await conn.execute("UPDATE ax_word_exchanges SET status='failed' WHERE id=$1", int(eid_s))
                    msg = "⚠️ The letters changed, so this swap can't happen anymore."
                else:
                    rl.remove(g_l); rl.append(w_l)
                    tl.remove(w_l); tl.append(g_l)
                    rdone, tdone = _check_word_complete(rt["target_word"], rl), _check_word_complete(tt["target_word"], tl)
                    for uid, ls, done in ((rid, rl, rdone), (tid, tl, tdone)):
                        await conn.execute("UPDATE ax_daily_tasks SET letters_json=$4, task1_done=$5, updated_at=now() WHERE bot_id=$1 AND user_id=$2 AND day_key=$3", bid, uid, ex["day_key"], json.dumps(ls), done)
                    await conn.execute("UPDATE ax_word_exchanges SET status='completed' WHERE id=$1", int(eid_s))
                    paid = [await _settle_both_reward_if_ready(conn, uid, bid, ex["day_key"]) for uid in (rid, tid)]
                    msg = f"✅ Swapped! <b>{esc(ex['requester_name'])}</b> got <code>{w_l}</code>, <b>{esc(ex['target_name'])}</b> got <code>{g_l}</code>."
                    if rdone:
                        msg += f"\n🎉 {esc(ex['requester_name'])} finished the word task!"
                    if tdone:
                        msg += f"\n🎉 {esc(ex['target_name'])} finished the word task!"
                    if any(paid):
                        msg += f"\n🎁 Reward <b>${REWARD:,}</b> paid!"
    await query.answer()
    await _finish_msg(query, msg or "Done.")


def register(dp):
    if router.parent_router is None:
        dp.include_router(router)

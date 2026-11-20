"""Chat events for Rumi: Blood Pacts, Underworld Bounties, Flash Heists (aiogram 3.x + APScheduler)."""
import asyncio
import html
import logging
import random
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import services
from ax_common import cmd_args

log = logging.getLogger(__name__)
router = Router(name="chat_events")

# ---- banners (Telegram file_ids from your DM bot) ----
BANNER_PACT = "AgACAgUAAxkBAAIQ8GrUt-AtfUycvKCrm31xPSuFSPLiAALnEWsbjaaoVoF1achx5h9AAQADAgADeQADPQQ"
BANNER_BOUNTY = "AgACAgUAAxkBAAIQ8mrUuBB7Wu8Dt_hTquhyU8TtFoPSAALoEWsbjaaoVv86QfdWbrqvAQADAgADeQADPQQ"
BANNER_HEIST = "AgACAgUAAxkBAAIQ9GrUvuE9Uvt92A7OTl96CnrUOjmGAAL1EWsbjaaoVk_07OGThb7vAQADAgADeQADPQQ"

# ---- tuning ----
PACT_BETRAY_COST = 50_000
PACT_BONUS = 0.05
BOUNTY_MIN = 1_000
HEIST_CARGO = 10_000
HEIST_CREW = 3
HEIST_MIN_DELAY = 4 * 3600      # next heist 4 to 5 hours after the last
HEIST_MAX_DELAY = 5 * 3600
HEIST_FIRST_MIN = 1 * 3600      # first heist after a restart
HEIST_FIRST_MAX = 3 * 3600
HEIST_EXPIRE = 15 * 60          # truck escapes if the crew is not full

_heists: dict[tuple[int, int], dict] = {}


# ------------------------------------------------------------------ helpers
def _money(n: int) -> str:
    return f"${n:,}"


def _name(user) -> str:
    return html.escape(user.first_name or "Player")


def _kb(label: str, data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data)]])


async def _reply_card(msg: Message, photo: str | None, text: str, kb: InlineKeyboardMarkup | None = None):
    if photo:
        try:
            return await msg.reply_photo(photo=photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            log.warning("banner send failed, falling back to text", exc_info=True)
    return await msg.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _bot_card(bot: Bot, chat_id: int, photo: str | None, text: str, kb: InlineKeyboardMarkup | None = None):
    if photo:
        try:
            return await bot.send_photo(chat_id, photo=photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            log.warning("banner send failed, falling back to text", exc_info=True)
    return await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _edit_card(message: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        if message.photo:
            await message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        log.warning("card edit failed", exc_info=True)


async def _notice(msg: Message, text: str):
    return await msg.reply(f"<blockquote>{text}</blockquote>", parse_mode=ParseMode.HTML)


async def _first_name(bot: Bot, chat_id: int, uid: int, pool: asyncpg.Pool) -> str:
    try:
        member = await bot.get_chat_member(chat_id, uid)
        if member.user.first_name:
            return html.escape(member.user.first_name)
    except Exception:
        pass
    try:
        return html.escape(str(await services.get_display_name(pool, uid)))
    except Exception:
        return "Player"


async def _ensure(pool: asyncpg.Pool, user) -> None:
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)


async def _lock_wallets(conn, ids):
    """Lock wallets lowest id first (same order as _transfer_core) so nothing deadlocks."""
    rows = {}
    for uid in sorted(set(ids)):
        rows[uid] = await conn.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
    return rows


async def _pact_of(conn, uid: int):
    return await conn.fetchrow(
        "SELECT player1_id, player2_id FROM blood_pacts WHERE player1_id=$1 OR player2_id=$1",
        uid,
    )


# ------------------------------------------------------------ public hooks
async def pact_multiplier(pool: asyncpg.Pool, user_id: int) -> float:
    """1.05 if the user has an active Blood Pact, else 1.0 (use on job payouts)."""
    async with pool.acquire() as conn:
        row = await _pact_of(conn, user_id)
    return 1.0 + PACT_BONUS if row else 1.0


async def claim_bounty(conn, robber_id: int, target_id: int) -> int:
    """Call inside the /rob transaction after a SUCCESSFUL rob. Returns coins paid (0 if none)."""
    amount = await conn.fetchval(
        "DELETE FROM bounties WHERE target_id=$1 RETURNING bounty_amount",
        target_id,
    )
    if not amount:
        return 0
    wallet = await conn.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", robber_id)
    if wallet is None:
        return 0
    await conn.execute(
        "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id=$2",
        amount,
        robber_id,
    )
    await services.insert_transaction(
        conn,
        user_id=robber_id,
        type_="BOUNTY_CLAIMED",
        currency="cash",
        amount=amount,
        balance_before=wallet["cash"],
        balance_after=wallet["cash"] + amount,
        related_user_id=target_id,
        source="rob_bounty",
    )
    return int(amount)


# ------------------------------------------------------------ FEATURE 1: PACTS
@router.message(Command("pact"))
async def pact_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if target is None:
        await _notice(
            message,
            "🩸 <b>Reply to someone</b> with /pact to offer them a Blood Pact, "
            + html.escape(user.first_name or "friend")
            + " 💞",
        )
        return
    if target.is_bot or target.id == user.id:
        await _notice(message, "🩸 A pact needs <b>two real people</b>, " + _name(user) + " 😜")
        return

    await _ensure(pool, user)
    await _ensure(pool, target)
    async with pool.acquire() as conn:
        mine = await _pact_of(conn, user.id)
        theirs = await _pact_of(conn, target.id)
    if mine:
        await _notice(
            message,
            f"🩸 {_name(user)}, you already have a Blood Pact. Use /betray to break it first 💀",
        )
        return
    if theirs:
        await _notice(message, f"🩸 {_name(target)} has already sworn loyalty to someone else 💔")
        return

    text = (
        "🩸 <b>BLOOD PACT OFFERED</b>\n"
        f"<blockquote>{_name(user)} wants to swear loyalty to {_name(target)}.\n"
        f"💀 <i>Betrayal will cost {_money(PACT_BETRAY_COST)} Coins.</i>\n"
        f"<i>Only {_name(target)} can accept.</i></blockquote>"
    )
    await _reply_card(message, BANNER_PACT, text, _kb("🩸 Accept Pact", f"ce:pact:{user.id}:{target.id}"))


async def _pact_accept(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool, proposer: int, target: int) -> None:
    if query.from_user.id != target:
        await query.answer("This offer isn't for you 🩸", show_alert=True)
        return
    status = "ok"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock_wallets(conn, [proposer, target])  # serialises pact changes for both
            if await _pact_of(conn, proposer) or await _pact_of(conn, target):
                status = "busy"
            else:
                await conn.execute(
                    "INSERT INTO blood_pacts (player1_id, player2_id) VALUES ($1, $2)",
                    proposer,
                    target,
                )
    if status == "busy":
        await query.answer("One of you already has a pact 💔", show_alert=True)
        if query.message:
            await _edit_card(
                query.message,
                "🩸 <b>BLOOD PACT OFFER EXPIRED</b>\n<blockquote><i>One of you is already sworn to someone else.</i></blockquote>",
            )
        return
    await query.answer("Pact forged 🩸")
    if query.message:
        a = await _first_name(bot, query.message.chat.id, proposer, pool)
        b = _name(query.from_user)
        text = (
            "🩸 <b>BLOOD PACT FORGED</b>\n"
            f"<blockquote>{a} and {b} have sworn loyalty to each other.\n"
            f"✨ <b>Perk:</b> <i>+{int(PACT_BONUS * 100)}% Syndicate Synergy on jobs.</i>\n"
            f"💀 <i>Betrayal will cost {_money(PACT_BETRAY_COST)} Coins.</i></blockquote>"
        )
        await _edit_card(query.message, text)


@router.message(Command("betray"))
async def betray_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    await _ensure(pool, user)
    status, partner = "ok", None
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, [user.id])
            cash = wallets[user.id]["cash"]
            pact = await _pact_of(conn, user.id)
            if pact is None:
                status = "none"
            else:
                partner = pact["player2_id"] if pact["player1_id"] == user.id else pact["player1_id"]
                if cash < PACT_BETRAY_COST:
                    status = "poor"
                else:
                    await conn.execute(
                        "DELETE FROM blood_pacts WHERE player1_id=$1 OR player2_id=$1",
                        user.id,
                    )
                    await conn.execute(
                        "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id=$2",
                        PACT_BETRAY_COST,
                        user.id,
                    )
                    await services.insert_transaction(
                        conn,
                        user_id=user.id,
                        type_="PACT_BETRAYAL",
                        currency="cash",
                        amount=-PACT_BETRAY_COST,
                        balance_before=cash,
                        balance_after=cash - PACT_BETRAY_COST,
                        related_user_id=partner,
                        source="betray_command",
                    )

    if status == "none":
        await _notice(message, f"🩸 {_name(user)}, you have no Blood Pact to betray 😌")
    elif status == "poor":
        await _notice(
            message,
            f"💸 Betrayal costs <code>{_money(PACT_BETRAY_COST)}</code> Coins and your wallet is short, {_name(user)}. The pact stays 🩸",
        )
    else:
        pname = await _first_name(bot, message.chat.id, partner, pool)
        await _notice(
            message,
            "💔 <b>PACT BROKEN</b>\n"
            f"{_name(user)} betrayed {pname}.\n"
            f"💸 <b>Price paid:</b> <code>{_money(PACT_BETRAY_COST)} Coins</code>\n"
            '<i>Rumi says: "Loyalty is priceless... betrayal is $50,000." 😜</i>',
        )


# ---------------------------------------------------------- FEATURE 2: BOUNTIES
@router.message(Command("bounty"))
async def bounty_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    target = message.reply_to_message.from_user if message.reply_to_message else None
    args = cmd_args(message)
    amount = None
    if args:
        try:
            amount = int(args[-1].replace(",", "").replace("$", ""))
        except ValueError:
            amount = None
    if target is None or amount is None:
        await _notice(
            message,
            "📜 Reply to someone with <code>/bounty amount</code> to put a price on their head, "
            + _name(user)
            + " 🎯",
        )
        return
    if target.is_bot or target.id == user.id:
        await _notice(message, "📜 Pick a <b>real target</b> that isn't yourself, " + _name(user) + " 😜")
        return
    if amount < BOUNTY_MIN:
        await _notice(
            message,
            f"📜 The minimum bounty is <code>{_money(BOUNTY_MIN)} Coins</code>, {_name(user)} 💰",
        )
        return

    await _ensure(pool, user)
    total = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, [user.id])
            cash = wallets[user.id]["cash"]
            if cash >= amount:
                await conn.execute(
                    "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id=$2",
                    amount,
                    user.id,
                )
                total = await conn.fetchval(
                    "INSERT INTO bounties (target_id, bounty_amount) VALUES ($1, $2) "
                    "ON CONFLICT (target_id) DO UPDATE SET bounty_amount = bounties.bounty_amount + EXCLUDED.bounty_amount "
                    "RETURNING bounty_amount",
                    target.id,
                    amount,
                )
                await services.insert_transaction(
                    conn,
                    user_id=user.id,
                    type_="BOUNTY_PLACED",
                    currency="cash",
                    amount=-amount,
                    balance_before=cash,
                    balance_after=cash - amount,
                    related_user_id=target.id,
                    source="bounty_command",
                )
    if total is None:
        await _notice(message, f"💸 Your wallet is too light for that bounty, {_name(user)} 🥺")
        return
    extra = f"\n➕ <b>Added now:</b> <code>{_money(amount)}</code>" if total != amount else ""
    text = (
        "📜 <b>NEW BOUNTY ISSUED</b>\n"
        f"<blockquote>🎯 <b>Target:</b> {_name(target)}\n"
        f"💰 <b>Reward:</b> <code>{_money(total)} Coins</code>{extra}\n"
        '<i>Rumi says: "Someone really doesn\'t like you... Watch your back! 😜"</i></blockquote>'
    )
    await _reply_card(message, BANNER_BOUNTY, text)


# ------------------------------------------------------- FEATURE 3: FLASH HEISTS
def _heist_text(crew: list[tuple[int, str]]) -> str:
    names = ", ".join(n for _, n in crew)
    crew_line = f"\n🔫 <b>Crew:</b> {names}" if crew else ""
    return (
        "🚨 <b>ARMORED TRUCK SPOTTED</b>\n"
        '<blockquote><i>Rumi says: "An unguarded syndicate transport is passing through the chat! '
        'Who is fast enough?"</i>\n'
        f"💰 <b>Cargo:</b> <code>{_money(HEIST_CARGO)} Coins</code>\n"
        f"👥 <b>Crew Needed:</b> <code>{len(crew)}/{HEIST_CREW}</code>{crew_line}</blockquote>"
    )


def _heist_kb() -> InlineKeyboardMarkup:
    return _kb("🔫 Join the Heist", "ce:heist")


def _schedule_next(scheduler: AsyncIOScheduler, bot: Bot, pool: asyncpg.Pool, delay: int | None = None) -> None:
    wait = delay if delay is not None else random.randint(HEIST_MIN_DELAY, HEIST_MAX_DELAY)
    scheduler.add_job(
        heist_job,
        trigger="date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=wait),
        args=[bot, pool, scheduler],
        id="flash_heist",
        replace_existing=True,
    )


async def heist_job(bot: Bot, pool: asyncpg.Pool, scheduler: AsyncIOScheduler) -> None:
    """Picks ONE random group from ax_groups and drops a heist. Re-arms itself 4-5h later."""
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT chat_id FROM ax_groups "
                "WHERE chat_id NOT IN (SELECT chat_id FROM ax_channels) "
                "ORDER BY random() LIMIT 5"
            )
        for row in rows:  # first group that accepts the message wins
            chat_id = row["chat_id"]
            try:
                c = await bot.get_chat(chat_id)
                if getattr(c, "type", "") == "channel":
                    continue
            except Exception:
                continue
            try:
                sent = await _bot_card(bot, chat_id, BANNER_HEIST, _heist_text([]), _heist_kb())
            except Exception:
                log.info("heist: cannot post in %s, trying another group", chat_id)
                continue
            _heists[(chat_id, sent.message_id)] = {
                "crew": [],
                "done": False,
                "lock": asyncio.Lock(),
                "msg": sent,
            }
            scheduler.add_job(
                _heist_expire,
                trigger="date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=HEIST_EXPIRE),
                args=[chat_id, sent.message_id],
                id=f"heist_exp:{chat_id}:{sent.message_id}",
                replace_existing=True,
            )
            break
    except Exception:
        log.exception("heist_job failed")
    finally:
        _schedule_next(scheduler, bot, pool)


async def _heist_expire(chat_id: int, mid: int) -> None:
    h = _heists.get((chat_id, mid))
    if not h:
        return
    async with h["lock"]:
        if h["done"]:
            return
        h["done"] = True
    _heists.pop((chat_id, mid), None)
    await _edit_card(
        h["msg"],
        '💨 <b>THE TRUCK GOT AWAY</b>\n<blockquote><i>Rumi says: "Too slow... the crew never assembled." 😜</i></blockquote>',
    )


async def _pay_crew(pool: asyncpg.Pool, chat_id: int, mid: int, crew: list[tuple[int, str]]) -> int:
    share = HEIST_CARGO // HEIST_CREW
    ids = [uid for uid, _ in crew]
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, ids)
            for uid in ids:
                before = wallets[uid]["cash"]
                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id=$2",
                    share,
                    uid,
                )
                await services.insert_transaction(
                    conn,
                    user_id=uid,
                    type_="HEIST_PAYOUT",
                    currency="cash",
                    amount=share,
                    balance_before=before,
                    balance_after=before + share,
                    source="flash_heist",
                    idempotency_key=f"heist:{chat_id}:{mid}:{uid}",
                )
    return share


async def _heist_join(query: CallbackQuery, pool: asyncpg.Pool, scheduler: AsyncIOScheduler) -> None:
    msg = query.message
    user = query.from_user
    if msg is None:
        await query.answer()
        return
    key = (msg.chat.id, msg.message_id)
    h = _heists.get(key)
    if not h or h["done"]:
        await query.answer("This heist is already over 💨", show_alert=True)
        return
    await _ensure(pool, user)
    async with h["lock"]:
        if h["done"]:
            await query.answer("This heist is already over 💨", show_alert=True)
            return
        if user.id in {uid for uid, _ in h["crew"]}:
            await query.answer("You're already in the crew 🔫", show_alert=True)
            return
        h["crew"].append((user.id, _name(user)))
        if len(h["crew"]) < HEIST_CREW:
            await query.answer("You're in the crew! 🔫")
            await _edit_card(msg, _heist_text(h["crew"]), _heist_kb())
            return
        h["done"] = True
        crew = list(h["crew"])

    await query.answer("Crew complete! 🚨")
    try:
        scheduler.remove_job(f"heist_exp:{key[0]}:{key[1]}")
    except JobLookupError:
        pass
    _heists.pop(key, None)

    try:
        share = await _pay_crew(pool, key[0], key[1], crew)
    except Exception:
        log.exception("heist payout failed")
        await _edit_card(
            msg,
            "🚨 <b>HEIST JAMMED</b>\n<blockquote><i>The truck's lock held and no coins moved. Try the next one!</i></blockquote>",
        )
        return

    names = ", ".join(n for _, n in crew)
    await _edit_card(
        msg,
        "✅ <b>HEIST SUCCESSFUL</b>\n"
        f"<blockquote>The crew cracked the truck wide open! 🚨\n👥 <b>Crew:</b> {names}\n"
        f"💰 <b>Payout:</b> <code>{_money(share)} Coins</code> each</blockquote>",
    )


# ------------------------------------------------------------------ routing
@router.callback_query(F.data.startswith("ce:"))
async def ce_callback(
    query: CallbackQuery,
    bot: Bot,
    pool: asyncpg.Pool,
    scheduler: AsyncIOScheduler,
) -> None:
    parts = (query.data or "").split(":")
    try:
        if len(parts) == 4 and parts[1] == "pact":
            await _pact_accept(query, bot, pool, int(parts[2]), int(parts[3]))
        elif len(parts) >= 2 and parts[1] == "heist":
            await _heist_join(query, pool, scheduler)
        else:
            await query.answer()
    except Exception:
        log.exception("chat_events callback failed")
        try:
            await query.answer("Something slipped, try again 💫", show_alert=True)
        except Exception:
            pass


async def on_startup(bot: Bot, dispatcher) -> None:
    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    pool: asyncpg.Pool = dispatcher["pool"]
    _schedule_next(scheduler, bot, pool, delay=random.randint(HEIST_FIRST_MIN, HEIST_FIRST_MAX))


async def on_shutdown() -> None:
    _heists.clear()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

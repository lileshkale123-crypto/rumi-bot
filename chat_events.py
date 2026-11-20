"""Chat events: Blood Pacts, Underworld Bounties, Flash Heists (aiogram 3.x + APScheduler, bot_id & brand aware)."""
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

import bot_context
import services
from ax_common import cmd_args
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="chat_events")

BANNER_PACT = "AgACAgUAAxkBAAIQ8GrUt-AtfUycvKCrm31xPSuFSPLiAALnEWsbjaaoVoF1achx5h9AAQADAgADeQADPQQ"
BANNER_BOUNTY = "AgACAgUAAxkBAAIQ8mrUuBB7Wu8Dt_hTquhyU8TtFoPSAALoEWsbjaaoVv86QfdWbrqvAQADAgADeQADPQQ"
BANNER_HEIST = "AgACAgUAAxkBAAIQ9GrUvuE9Uvt92A7OTl96CnrUOjmGAAL1EWsbjaaoVk_07OGThb7vAQADAgADeQADPQQ"

PACT_BETRAY_COST = 50_000
PACT_BONUS = 0.05
BOUNTY_MIN = 1_000
HEIST_CARGO = 10_000
HEIST_CREW = 3
HEIST_MIN_DELAY = 4 * 3600
HEIST_MAX_DELAY = 5 * 3600
HEIST_FIRST_MIN = 1 * 3600
HEIST_FIRST_MAX = 3 * 3600
HEIST_EXPIRE = 15 * 60

# Keyed by (bot_id, chat_id, message_id) so clones never share active heists
_heists: dict[tuple[int, int, int], dict] = {}


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


async def _first_name(bot: Bot, chat_id: int, uid: int, pool: asyncpg.Pool, bot_id: int) -> str:
    try:
        member = await bot.get_chat_member(chat_id, uid)
        if member.user.first_name:
            return html.escape(member.user.first_name)
    except Exception:
        pass
    try:
        return html.escape(str(await services.get_display_name(pool, uid, bot_id=bot_id)))
    except Exception:
        return "Player"


async def _ensure(pool: asyncpg.Pool, user, bot_id: int) -> None:
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bot_id)


async def _lock_wallets(conn, ids, bot_id: int):
    rows = {}
    for uid in sorted(set(ids)):
        rows[uid] = await conn.fetchrow(
            "SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
            bot_id,
            uid,
        )
    return rows


async def _pact_of(conn, uid: int, bot_id: int):
    return await conn.fetchrow(
        "SELECT player1_id, player2_id FROM blood_pacts "
        "WHERE bot_id=$1 AND (player1_id=$2 OR player2_id=$2)",
        bot_id,
        uid,
    )


async def pact_multiplier(pool: asyncpg.Pool, user_id: int, bot_id: int | None = None) -> float:
    """1.05 if the user has an active Blood Pact in this bot, else 1.0."""
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        row = await _pact_of(conn, user_id, bid)
    return 1.0 + PACT_BONUS if row else 1.0


async def claim_bounty(
    conn,
    robber_id: int,
    target_id: int,
    bot_id: int | None = None,
) -> int:
    """Call inside the /rob transaction after a SUCCESSFUL rob. Returns coins paid (0 if none)."""
    bid = bot_context.resolve_bot_id(bot_id)
    amount = await conn.fetchval(
        "DELETE FROM bounties WHERE bot_id=$1 AND target_id=$2 RETURNING bounty_amount",
        bid,
        target_id,
    )
    if not amount:
        return 0
    wallet = await conn.fetchrow(
        "SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
        bid,
        robber_id,
    )
    if wallet is None:
        return 0
    await conn.execute(
        "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
        amount,
        bid,
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
        bot_id=bid,
    )
    return int(amount)


# ------------------------------------------------------------ FEATURE 1: PACTS
@router.message(Command("pact"))
async def pact_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
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

    bid = bot_ctx.bot_id
    await _ensure(pool, user, bid)
    await _ensure(pool, target, bid)
    async with pool.acquire() as conn:
        mine = await _pact_of(conn, user.id, bid)
        theirs = await _pact_of(conn, target.id, bid)
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


async def _pact_accept(
    query: CallbackQuery,
    bot: Bot,
    pool: asyncpg.Pool,
    proposer: int,
    target: int,
    bid: int,
) -> None:
    if query.from_user.id != target:
        await query.answer("This offer isn't for you 🩸", show_alert=True)
        return
    status = "ok"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock_wallets(conn, [proposer, target], bid)
            if await _pact_of(conn, proposer, bid) or await _pact_of(conn, target, bid):
                status = "busy"
            else:
                await conn.execute(
                    "INSERT INTO blood_pacts (bot_id, player1_id, player2_id) VALUES ($1, $2, $3)",
                    bid,
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
        a = await _first_name(bot, query.message.chat.id, proposer, pool, bid)
        b = _name(query.from_user)
        text = (
            "🩸 <b>BLOOD PACT FORGED</b>\n"
            f"<blockquote>{a} and {b} have sworn loyalty to each other.\n"
            f"✨ <b>Perk:</b> <i>+{int(PACT_BONUS * 100)}% Syndicate Synergy on jobs.</i>\n"
            f"💀 <i>Betrayal will cost {_money(PACT_BETRAY_COST)} Coins.</i></blockquote>"
        )
        await _edit_card(query.message, text)


@router.message(Command("betray"))
async def betray_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    bname = html.escape(get_brand(bid).name)
    await _ensure(pool, user, bid)
    status, partner = "ok", None
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, [user.id], bid)
            cash = wallets[user.id]["cash"]
            pact = await _pact_of(conn, user.id, bid)
            if pact is None:
                status = "none"
            else:
                partner = pact["player2_id"] if pact["player1_id"] == user.id else pact["player1_id"]
                if cash < PACT_BETRAY_COST:
                    status = "poor"
                else:
                    await conn.execute(
                        "DELETE FROM blood_pacts WHERE bot_id=$1 AND (player1_id=$2 OR player2_id=$2)",
                        bid,
                        user.id,
                    )
                    await conn.execute(
                        "UPDATE wallets SET cash = cash - $1, updated_at = now() "
                        "WHERE bot_id=$2 AND user_id=$3",
                        PACT_BETRAY_COST,
                        bid,
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
                        bot_id=bid,
                    )

    if status == "none":
        await _notice(message, f"🩸 {_name(user)}, you have no Blood Pact to betray 😌")
    elif status == "poor":
        await _notice(
            message,
            f"💸 Betrayal costs <code>{_money(PACT_BETRAY_COST)}</code> Coins and your wallet is short, {_name(user)}. The pact stays 🩸",
        )
    else:
        pname = await _first_name(bot, message.chat.id, partner, pool, bid)
        await _notice(
            message,
            "💔 <b>PACT BROKEN</b>\n"
            f"{_name(user)} betrayed {pname}.\n"
            f"💸 <b>Price paid:</b> <code>{_money(PACT_BETRAY_COST)} Coins</code>\n"
            f'<i>{bname} says: "Loyalty is priceless... betrayal is $50,000." 😜</i>',
        )


# ---------------------------------------------------------- FEATURE 2: BOUNTIES
@router.message(Command("bounty"))
async def bounty_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
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

    bid = bot_ctx.bot_id
    bname = html.escape(get_brand(bid).name)
    await _ensure(pool, user, bid)
    total = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, [user.id], bid)
            cash = wallets[user.id]["cash"]
            if cash >= amount:
                await conn.execute(
                    "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                    amount,
                    bid,
                    user.id,
                )
                total = await conn.fetchval(
                    "INSERT INTO bounties (bot_id, target_id, bounty_amount) VALUES ($1, $2, $3) "
                    "ON CONFLICT (target_id) DO UPDATE SET "
                    "bot_id = EXCLUDED.bot_id, "
                    "bounty_amount = bounties.bounty_amount + EXCLUDED.bounty_amount "
                    "RETURNING bounty_amount",
                    bid,
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
                    bot_id=bid,
                )
    if total is None:
        await _notice(message, f"💸 Your wallet is too light for that bounty, {_name(user)} 🥺")
        return
    extra = f"\n➕ <b>Added now:</b> <code>{_money(amount)}</code>" if total != amount else ""
    text = (
        "📜 <b>NEW BOUNTY ISSUED</b>\n"
        f"<blockquote>🎯 <b>Target:</b> {_name(target)}\n"
        f"💰 <b>Reward:</b> <code>{_money(total)} Coins</code>{extra}\n"
        f'<i>{bname} says: "Someone really doesn\'t like you... Watch your back! 😜"</i></blockquote>'
    )
    await _reply_card(message, BANNER_BOUNTY, text)


# ------------------------------------------------------- FEATURE 3: FLASH HEISTS
def _heist_text(crew: list[tuple[int, str]], bot_id: int | None = None) -> str:
    bname = html.escape(get_brand(bot_id).name)
    names = ", ".join(n for _, n in crew)
    crew_line = f"\n🔫 <b>Crew:</b> {names}" if crew else ""
    return (
        "🚨 <b>ARMORED TRUCK SPOTTED</b>\n"
        f'<blockquote><i>{bname} says: "An unguarded syndicate transport is passing through the chat! '
        'Who is fast enough?"</i>\n'
        f"💰 <b>Cargo:</b> <code>{_money(HEIST_CARGO)} Coins</code>\n"
        f"👥 <b>Crew Needed:</b> <code>{len(crew)}/{HEIST_CREW}</code>{crew_line}</blockquote>"
    )


def _heist_kb() -> InlineKeyboardMarkup:
    return _kb("🔫 Join the Heist", "ce:heist")


def _schedule_next(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    pool: asyncpg.Pool,
    delay: int | None = None,
    bot_id: int | None = None,
) -> None:
    bid = bot_context.resolve_bot_id(bot_id if bot_id is not None else bot.id)
    wait = delay if delay is not None else random.randint(HEIST_MIN_DELAY, HEIST_MAX_DELAY)
    scheduler.add_job(
        heist_job,
        trigger="date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=wait),
        args=[bot, pool, scheduler, bid],
        id=f"flash_heist:{bid}",
        replace_existing=True,
    )


async def heist_job(
    bot: Bot,
    pool: asyncpg.Pool,
    scheduler: AsyncIOScheduler,
    bot_id: int | None = None,
) -> None:
    """Picks ONE random group from ax_groups for this bot and drops a heist."""
    bid = bot_context.resolve_bot_id(bot_id if bot_id is not None else bot.id)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT chat_id FROM ax_groups "
                "WHERE bot_id=$1 AND chat_id NOT IN (SELECT chat_id FROM ax_channels WHERE bot_id=$1) "
                "ORDER BY random() LIMIT 5",
                bid,
            )
        for row in rows:
            chat_id = row["chat_id"]
            try:
                c = await bot.get_chat(chat_id)
                if getattr(c, "type", "") == "channel":
                    continue
            except Exception:
                continue
            try:
                sent = await _bot_card(bot, chat_id, BANNER_HEIST, _heist_text([], bot_id=bid), _heist_kb())
            except Exception:
                log.info("heist: cannot post in %s, trying another group", chat_id)
                continue
            _heists[(bid, chat_id, sent.message_id)] = {
                "bot_id": bid,
                "crew": [],
                "done": False,
                "lock": asyncio.Lock(),
                "msg": sent,
            }
            scheduler.add_job(
                _heist_expire,
                trigger="date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=HEIST_EXPIRE),
                args=[bid, chat_id, sent.message_id],
                id=f"heist_exp:{bid}:{chat_id}:{sent.message_id}",
                replace_existing=True,
            )
            break
    except Exception:
        log.exception("heist_job failed")
    finally:
        _schedule_next(scheduler, bot, pool, bot_id=bid)


async def _heist_expire(bid: int, chat_id: int, mid: int) -> None:
    h = _heists.get((bid, chat_id, mid))
    if not h:
        return
    async with h["lock"]:
        if h["done"]:
            return
        h["done"] = True
    _heists.pop((bid, chat_id, mid), None)
    bname = html.escape(get_brand(bid).name)
    await _edit_card(
        h["msg"],
        f'💨 <b>THE TRUCK GOT AWAY</b>\n<blockquote><i>{bname} says: "Too slow... the crew never assembled." 😜</i></blockquote>',
    )


async def _pay_crew(
    pool: asyncpg.Pool,
    bid: int,
    chat_id: int,
    mid: int,
    crew: list[tuple[int, str]],
) -> int:
    share = HEIST_CARGO // HEIST_CREW
    ids = [uid for uid, _ in crew]
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, ids, bid)
            for uid in ids:
                before = wallets[uid]["cash"]
                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                    share,
                    bid,
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
                    idempotency_key=f"heist:{bid}:{chat_id}:{mid}:{uid}",
                    bot_id=bid,
                )
    return share


async def _heist_join(
    query: CallbackQuery,
    pool: asyncpg.Pool,
    scheduler: AsyncIOScheduler,
    bid: int,
) -> None:
    msg = query.message
    user = query.from_user
    if msg is None:
        await query.answer()
        return
    key = (bid, msg.chat.id, msg.message_id)
    h = _heists.get(key)
    if not h or h["done"]:
        await query.answer("This heist is already over 💨", show_alert=True)
        return
    await _ensure(pool, user, bid)
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
            await _edit_card(msg, _heist_text(h["crew"], bot_id=bid), _heist_kb())
            return
        h["done"] = True
        crew = list(h["crew"])

    await query.answer("Crew complete! 🚨")
    try:
        scheduler.remove_job(f"heist_exp:{bid}:{key[1]}:{key[2]}")
    except JobLookupError:
        pass
    _heists.pop(key, None)

    try:
        share = await _pay_crew(pool, bid, key[1], key[2], crew)
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


@router.callback_query(F.data.startswith("ce:"))
async def ce_callback(
    query: CallbackQuery,
    bot: Bot,
    pool: asyncpg.Pool,
    scheduler: AsyncIOScheduler,
    bot_ctx: BotContext,
) -> None:
    parts = (query.data or "").split(":")
    bid = bot_ctx.bot_id
    try:
        if len(parts) == 4 and parts[1] == "pact":
            await _pact_accept(query, bot, pool, int(parts[2]), int(parts[3]), bid)
        elif len(parts) >= 2 and parts[1] == "heist":
            await _heist_join(query, pool, scheduler, bid)
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
    bid = bot_context.resolve_bot_id(bot.id)
    _schedule_next(
        scheduler,
        bot,
        pool,
        delay=random.randint(HEIST_FIRST_MIN, HEIST_FIRST_MAX),
        bot_id=bid,
    )


async def on_shutdown() -> None:
    _heists.clear()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

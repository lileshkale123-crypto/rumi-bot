"""Chat events for Rumi: Blood Pacts, Underworld Bounties, Flash Heists.

Register in main.py:  import chat_events ; chat_events.register(application)

SQL (goes into schema.sql, applied at startup):

    CREATE TABLE IF NOT EXISTS blood_pacts (
        player1_id BIGINT NOT NULL,
        player2_id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (player1_id, player2_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pact_p1 ON blood_pacts(player1_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pact_p2 ON blood_pacts(player2_id);
    CREATE TABLE IF NOT EXISTS bounties (
        target_id BIGINT PRIMARY KEY,
        bounty_amount BIGINT NOT NULL CHECK (bounty_amount > 0)
    );

Flash Heists reuse your existing ax_groups table (filled by ax_track.py),
so no separate active_chats table is needed.

=== HOOK 1: BOUNTY INTO /rob ===
In services.rob(), inside the same transaction, right AFTER the robber's cash
was credited for a SUCCESSFUL rob, add:

    import chat_events
    bounty = await chat_events.claim_bounty(conn, robber_id, victim_id)
    # if bounty: show "+$bounty bounty claimed" in the success card

claim_bounty deletes the victim's bounty row and pays the robber, with
a transactions log row, using the connection you already hold.

=== HOOK 2: PACT PERK (+5% on jobs) ===
In your job/work command, after the payout amount is computed:

    import chat_events
    amount = int(amount * await chat_events.pact_multiplier(pool, user_id))
"""
import asyncio
import html
import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

import services

log = logging.getLogger(__name__)

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


# ------------------------------------------------------------------ helpers
def _money(n: int) -> str:
    return f"${n:,}"


def _name(user) -> str:
    return html.escape(user.first_name or "Player")


def _kb(label: str, data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)]])


async def _reply_card(msg, photo, text, kb=None):
    if photo:
        try:
            return await msg.reply_photo(photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            log.warning("banner send failed, falling back to text", exc_info=True)
    return await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _bot_card(bot, chat_id, photo, text, kb=None):
    if photo:
        try:
            return await bot.send_photo(chat_id, photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            log.warning("banner send failed, falling back to text", exc_info=True)
    return await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _edit_card(message, text, kb=None):
    try:
        if message.photo:
            await message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        log.warning("card edit failed", exc_info=True)


async def _notice(msg, text: str):
    return await msg.reply_text(f"<blockquote>{text}</blockquote>", parse_mode=ParseMode.HTML)


async def _first_name(context, chat_id, uid, pool) -> str:
    try:
        member = await context.bot.get_chat_member(chat_id, uid)
        if member.user.first_name:
            return html.escape(member.user.first_name)
    except Exception:
        pass
    try:
        return html.escape(str(await services.get_display_name(pool, uid)))
    except Exception:
        return "Player"


async def _ensure(pool, user):
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name)


async def _lock_wallets(conn, ids):
    """Lock wallets lowest id first (same order as _transfer_core) so nothing deadlocks."""
    rows = {}
    for uid in sorted(set(ids)):
        rows[uid] = await conn.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
    return rows


async def _pact_of(conn, uid):
    return await conn.fetchrow(
        "SELECT player1_id, player2_id FROM blood_pacts WHERE player1_id=$1 OR player2_id=$1", uid
    )


# ------------------------------------------------------------ public hooks
async def pact_multiplier(pool, user_id: int) -> float:
    """1.05 if the user has an active Blood Pact, else 1.0 (use on job payouts)."""
    async with pool.acquire() as conn:
        row = await _pact_of(conn, user_id)
    return 1.0 + PACT_BONUS if row else 1.0


async def claim_bounty(conn, robber_id: int, target_id: int) -> int:
    """Call inside the /rob transaction after a SUCCESSFUL rob. Returns coins paid (0 if none)."""
    amount = await conn.fetchval(
        "DELETE FROM bounties WHERE target_id=$1 RETURNING bounty_amount", target_id
    )
    if not amount:
        return 0
    wallet = await conn.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", robber_id)
    if wallet is None:
        return 0
    await conn.execute(
        "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id=$2", amount, robber_id
    )
    await services.insert_transaction(
        conn, user_id=robber_id, type_="BOUNTY_CLAIMED", currency="cash", amount=amount,
        balance_before=wallet["cash"], balance_after=wallet["cash"] + amount,
        related_user_id=target_id, source="rob_bounty",
    )
    return int(amount)


# ------------------------------------------------------------ FEATURE 1: PACTS
async def pact_cmd(update, context):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user:
        return
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None:
        await _notice(msg, "🩸 <b>Reply to someone</b> with /pact to offer them a Blood Pact, " + html.escape(user.first_name or "friend") + " 💞")
        return
    if target.is_bot or target.id == user.id:
        await _notice(msg, "🩸 A pact needs <b>two real people</b>, " + _name(user) + " 😜")
        return
    pool = context.bot_data["pool"]
    await _ensure(pool, user)
    await _ensure(pool, target)
    async with pool.acquire() as conn:
        mine = await _pact_of(conn, user.id)
        theirs = await _pact_of(conn, target.id)
    if mine:
        await _notice(msg, f"🩸 {_name(user)}, you already have a Blood Pact. Use /betray to break it first 💀")
        return
    if theirs:
        await _notice(msg, f"🩸 {_name(target)} has already sworn loyalty to someone else 💔")
        return
    text = (
        "🩸 <b>BLOOD PACT OFFERED</b>\n"
        f"<blockquote>{_name(user)} wants to swear loyalty to {_name(target)}.\n"
        f"💀 <i>Betrayal will cost {_money(PACT_BETRAY_COST)} Coins.</i>\n"
        f"<i>Only {_name(target)} can accept.</i></blockquote>"
    )
    await _reply_card(msg, BANNER_PACT, text, _kb("🩸 Accept Pact", f"ce:pact:{user.id}:{target.id}"))


async def _pact_accept(update, context, proposer: int, target: int):
    q = update.callback_query
    if q.from_user.id != target:
        await q.answer("This offer isn't for you 🩸", show_alert=True)
        return
    pool = context.bot_data["pool"]
    status = "ok"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock_wallets(conn, [proposer, target])   # serialises pact changes for both
            if await _pact_of(conn, proposer) or await _pact_of(conn, target):
                status = "busy"
            else:
                await conn.execute(
                    "INSERT INTO blood_pacts (player1_id, player2_id) VALUES ($1, $2)", proposer, target
                )
    if status == "busy":
        await q.answer("One of you already has a pact 💔", show_alert=True)
        await _edit_card(q.message, "🩸 <b>BLOOD PACT OFFER EXPIRED</b>\n<blockquote><i>One of you is already sworn to someone else.</i></blockquote>")
        return
    await q.answer("Pact forged 🩸")
    a = await _first_name(context, q.message.chat_id, proposer, pool)
    b = _name(q.from_user)
    text = (
        "🩸 <b>BLOOD PACT FORGED</b>\n"
        f"<blockquote>{a} and {b} have sworn loyalty to each other.\n"
        f"✨ <b>Perk:</b> <i>+{int(PACT_BONUS * 100)}% Syndicate Synergy on jobs.</i>\n"
        f"💀 <i>Betrayal will cost {_money(PACT_BETRAY_COST)} Coins.</i></blockquote>"
    )
    await _edit_card(q.message, text)


async def betray_cmd(update, context):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user:
        return
    pool = context.bot_data["pool"]
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
                        "DELETE FROM blood_pacts WHERE player1_id=$1 OR player2_id=$1", user.id
                    )
                    await conn.execute(
                        "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id=$2",
                        PACT_BETRAY_COST, user.id,
                    )
                    await services.insert_transaction(
                        conn, user_id=user.id, type_="PACT_BETRAYAL", currency="cash",
                        amount=-PACT_BETRAY_COST, balance_before=cash,
                        balance_after=cash - PACT_BETRAY_COST, related_user_id=partner,
                        source="betray_command",
                    )
    if status == "none":
        await _notice(msg, f"🩸 {_name(user)}, you have no Blood Pact to betray 😌")
    elif status == "poor":
        await _notice(msg, f"💸 Betrayal costs <code>{_money(PACT_BETRAY_COST)}</code> Coins and your wallet is short, {_name(user)}. The pact stays 🩸")
    else:
        pname = await _first_name(context, msg.chat_id, partner, pool)
        await _notice(
            msg,
            "💔 <b>PACT BROKEN</b>\n"
            f"{_name(user)} betrayed {pname}.\n"
            f"💸 <b>Price paid:</b> <code>{_money(PACT_BETRAY_COST)} Coins</code>\n"
            "<i>Rumi says: \"Loyalty is priceless... betrayal is $50,000.\" 😜</i>",
        )


# ---------------------------------------------------------- FEATURE 2: BOUNTIES
async def bounty_cmd(update, context):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user:
        return
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    args = context.args or []
    amount = None
    if args:
        try:
            amount = int(args[-1].replace(",", "").replace("$", ""))
        except ValueError:
            amount = None
    if target is None or amount is None:
        await _notice(msg, "📜 Reply to someone with <code>/bounty amount</code> to put a price on their head, " + _name(user) + " 🎯")
        return
    if target.is_bot or target.id == user.id:
        await _notice(msg, "📜 Pick a <b>real target</b> that isn't yourself, " + _name(user) + " 😜")
        return
    if amount < BOUNTY_MIN:
        await _notice(msg, f"📜 The minimum bounty is <code>{_money(BOUNTY_MIN)} Coins</code>, {_name(user)} 💰")
        return
    pool = context.bot_data["pool"]
    await _ensure(pool, user)
    total = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, [user.id])
            cash = wallets[user.id]["cash"]
            if cash >= amount:
                await conn.execute(
                    "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id=$2",
                    amount, user.id,
                )
                total = await conn.fetchval(
                    "INSERT INTO bounties (target_id, bounty_amount) VALUES ($1, $2) "
                    "ON CONFLICT (target_id) DO UPDATE SET bounty_amount = bounties.bounty_amount + EXCLUDED.bounty_amount "
                    "RETURNING bounty_amount",
                    target.id, amount,
                )
                await services.insert_transaction(
                    conn, user_id=user.id, type_="BOUNTY_PLACED", currency="cash", amount=-amount,
                    balance_before=cash, balance_after=cash - amount,
                    related_user_id=target.id, source="bounty_command",
                )
    if total is None:
        await _notice(msg, f"💸 Your wallet is too light for that bounty, {_name(user)} 🥺")
        return
    extra = f"\n➕ <b>Added now:</b> <code>{_money(amount)}</code>" if total != amount else ""
    text = (
        "📜 <b>NEW BOUNTY ISSUED</b>\n"
        f"<blockquote>🎯 <b>Target:</b> {_name(target)}\n"
        f"💰 <b>Reward:</b> <code>{_money(total)} Coins</code>{extra}\n"
        "<i>Rumi says: \"Someone really doesn't like you... Watch your back! 😜\"</i></blockquote>"
    )
    await _reply_card(msg, BANNER_BOUNTY, text)


# ------------------------------------------------------- FEATURE 3: FLASH HEISTS
def _heist_text(crew) -> str:
    names = ", ".join(n for _, n in crew)
    crew_line = f"\n🔫 <b>Crew:</b> {names}" if crew else ""
    return (
        "🚨 <b>ARMORED TRUCK SPOTTED</b>\n"
        "<blockquote><i>Rumi says: \"An unguarded syndicate transport is passing through the chat! "
        "Who is fast enough?\"</i>\n"
        f"💰 <b>Cargo:</b> <code>{_money(HEIST_CARGO)} Coins</code>\n"
        f"👥 <b>Crew Needed:</b> <code>{len(crew)}/{HEIST_CREW}</code>{crew_line}</blockquote>"
    )


def _heist_kb() -> InlineKeyboardMarkup:
    return _kb("🔫 Join the Heist", "ce:heist")


def _schedule_next(job_queue):
    if job_queue:
        job_queue.run_once(heist_job, random.randint(HEIST_MIN_DELAY, HEIST_MAX_DELAY), name="flash_heist")


async def heist_job(context):
    """Picks ONE random group from ax_groups and drops a heist. Re-arms itself 4-5h later."""
    try:
        pool = context.bot_data["pool"]
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT chat_id FROM ax_groups ORDER BY random() LIMIT 5")
        for row in rows:                       # first group that accepts the message wins
            chat_id = row["chat_id"]
            try:
                sent = await _bot_card(context.bot, chat_id, BANNER_HEIST, _heist_text([]), _heist_kb())
            except Exception:
                log.info("heist: cannot post in %s, trying another group", chat_id)
                continue
            heists = context.bot_data.setdefault("ce_heists", {})
            heists[(chat_id, sent.message_id)] = {
                "crew": [], "done": False, "lock": asyncio.Lock(), "msg": sent,
            }
            if context.job_queue:
                context.job_queue.run_once(
                    _heist_expire, HEIST_EXPIRE, data=(chat_id, sent.message_id),
                    name=f"heist_exp:{chat_id}:{sent.message_id}",
                )
            break
    except Exception:
        log.exception("heist_job failed")
    finally:
        _schedule_next(context.job_queue)


async def _heist_expire(context):
    chat_id, mid = context.job.data
    heists = context.bot_data.get("ce_heists", {})
    h = heists.get((chat_id, mid))
    if not h:
        return
    async with h["lock"]:
        if h["done"]:
            return
        h["done"] = True
    heists.pop((chat_id, mid), None)
    await _edit_card(
        h["msg"],
        "💨 <b>THE TRUCK GOT AWAY</b>\n<blockquote><i>Rumi says: \"Too slow... the crew never assembled.\" 😜</i></blockquote>",
    )


async def _pay_crew(pool, chat_id, mid, crew):
    share = HEIST_CARGO // HEIST_CREW
    ids = [uid for uid, _ in crew]
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallets = await _lock_wallets(conn, ids)
            for uid in ids:
                before = wallets[uid]["cash"]
                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id=$2", share, uid
                )
                await services.insert_transaction(
                    conn, user_id=uid, type_="HEIST_PAYOUT", currency="cash", amount=share,
                    balance_before=before, balance_after=before + share,
                    source="flash_heist", idempotency_key=f"heist:{chat_id}:{mid}:{uid}",
                )
    return share


async def _heist_join(update, context):
    q = update.callback_query
    msg, user = q.message, q.from_user
    heists = context.bot_data.setdefault("ce_heists", {})
    key = (msg.chat_id, msg.message_id)
    h = heists.get(key)
    if not h or h["done"]:
        await q.answer("This heist is already over 💨", show_alert=True)
        return
    pool = context.bot_data["pool"]
    await _ensure(pool, user)
    async with h["lock"]:
        if h["done"]:
            await q.answer("This heist is already over 💨", show_alert=True)
            return
        if user.id in {uid for uid, _ in h["crew"]}:
            await q.answer("You're already in the crew 🔫", show_alert=True)
            return
        h["crew"].append((user.id, _name(user)))
        if len(h["crew"]) < HEIST_CREW:
            await q.answer("You're in the crew! 🔫")
            await _edit_card(msg, _heist_text(h["crew"]), _heist_kb())
            return
        h["done"] = True
        crew = list(h["crew"])
    await q.answer("Crew complete! 🚨")
    for job in (context.job_queue.get_jobs_by_name(f"heist_exp:{key[0]}:{key[1]}") if context.job_queue else []):
        job.schedule_removal()
    heists.pop(key, None)
    try:
        share = await _pay_crew(pool, key[0], key[1], crew)
    except Exception:
        log.exception("heist payout failed")
        await _edit_card(msg, "🚨 <b>HEIST JAMMED</b>\n<blockquote><i>The truck's lock held and no coins moved. Try the next one!</i></blockquote>")
        return
    names = ", ".join(n for _, n in crew)
    await _edit_card(
        msg,
        "✅ <b>HEIST SUCCESSFUL</b>\n"
        f"<blockquote>The crew cracked the truck wide open! 🚨\n👥 <b>Crew:</b> {names}\n"
        f"💰 <b>Payout:</b> <code>{_money(share)} Coins</code> each</blockquote>",
    )


# ------------------------------------------------------------------ routing
async def ce_callback(update, context):
    q = update.callback_query
    if not q or not q.data:
        return
    parts = q.data.split(":")
    try:
        if parts[1] == "pact" and len(parts) == 4:
            await _pact_accept(update, context, int(parts[2]), int(parts[3]))
        elif parts[1] == "heist":
            await _heist_join(update, context)
        else:
            await q.answer()
    except Exception:
        log.exception("chat_events callback failed")
        try:
            await q.answer("Something slipped, try again 💫", show_alert=True)
        except Exception:
            pass


def register(application):
    application.add_handler(CommandHandler("pact", pact_cmd))
    application.add_handler(CommandHandler("betray", betray_cmd))
    application.add_handler(CommandHandler("bounty", bounty_cmd))
    application.add_handler(CallbackQueryHandler(ce_callback, pattern=r"^ce:"))
    if application.job_queue:
        application.job_queue.run_once(
            heist_job, random.randint(HEIST_FIRST_MIN, HEIST_FIRST_MAX), name="flash_heist"
        )
    else:
        log.warning("job_queue missing: Flash Heists disabled")


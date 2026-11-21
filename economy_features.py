"""Vault, /work, /spin, /convert, /toplegends and /mafiatop (aiogram 3.x, bot_id & brand aware)."""

import asyncio
import random
from datetime import datetime, timedelta, timezone

import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import ax_common
import bot_context
import config
import services
import ui
from bot_context import BotContext, get_brand

router = Router(name="economy_features")
CUR = config.CURRENCY_SYMBOL

def _n(v) -> str:
    return f"{int(v):,}"


def _left(target: datetime) -> str:
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    secs = max(0, int((target - datetime.now(timezone.utc)).total_seconds()))
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def vault_cap(level: int) -> int:
    """New Season Vault rule: maximum Vault storage limit is 20,000 Coins."""
    return min(
        getattr(config, "VAULT_MAX_CAPACITY", 20_000),
        config.VAULT_BASE_CAPACITY + level * config.VAULT_CAPACITY_PER_LEVEL,
    )


async def _prep(message: Message, pool: asyncpg.Pool, bot_id: int | None = None):
    user = message.from_user
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bot_id)
    return user


async def _ensure_season_tables(conn) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS ax_season_gem_conv ("
        "bot_id BIGINT NOT NULL DEFAULT 0, "
        "user_id BIGINT NOT NULL, "
        "converted_gems INT NOT NULL DEFAULT 0, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (bot_id, user_id))"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS ax_season_legends ("
        "id BIGSERIAL PRIMARY KEY, "
        "bot_id BIGINT NOT NULL DEFAULT 0, "
        "season_tag TEXT NOT NULL, "
        "user_id BIGINT NOT NULL, "
        "display_name TEXT NOT NULL, "
        "cash_snapshot BIGINT NOT NULL DEFAULT 0, "
        "gems_snapshot BIGINT NOT NULL DEFAULT 0, "
        "xp_snapshot BIGINT NOT NULL DEFAULT 0, "
        "level_snapshot INT NOT NULL DEFAULT 1, "
        "recorded_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )


@router.message(Command("vault"))
async def vault_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if message.from_user is None:
        return
    bid = bot_ctx.bot_id
    u = await _prep(message, pool, bot_id=bid)
    w = await services.get_wallet(pool, u.id, bot_id=bid)
    cap = vault_cap(w["level"])
    text = (
        "🏦 <b>THE UNDERWORLD VAULT</b>\n"
        "<blockquote>"
        f"👤 <b>Client:</b> {ui.escape_html(u.first_name or 'Player')}\n"
        f"📈 <b>Vault Level:</b> {w['level']}\n"
        f"💰 <b>Liquid Cash:</b> <code>{CUR}{_n(w['cash'])}</code>\n"
        f"🔐 <b>Secured Assets:</b> <code>{CUR}{_n(w['vault'])} / {CUR}{_n(cap)}</code>"
        "</blockquote>\n"
        "<i>Max limit: <b>20,000</b> Coins • Use /deposit [amount] or /withdraw [amount].</i>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


def _parse_amount(args: list[str]):
    """Returns (ok, amount). amount None means 'all'."""
    if not args:
        return False, 0
    a = args[0].lower().replace(",", "")
    if a == "all":
        return True, None
    if a.isdigit() and int(a) > 0:
        return True, int(a)
    return False, 0


async def _move(pool: asyncpg.Pool, uid: int, amount, deposit: bool, bot_id: int | None = None) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            w = await conn.fetchrow(
                "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                bid,
                uid,
            )
            if w is None:
                return {"status": "error"}
            cap = vault_cap(w["level"])
            cash, vault = w["cash"], w["vault"]
            if deposit:
                room = cap - vault
                if room <= 0:
                    return {"status": "full", "cap": cap}
                if amount is None:
                    amount = min(cash, room)
                if amount <= 0 or amount > cash:
                    return {"status": "no_cash"}
                if amount > room:
                    return {"status": "over_cap", "room": room}
                new_cash, new_vault = cash - amount, vault + amount
                t, src, delta = "VAULT_DEPOSIT", "deposit_command", -amount
            else:
                if amount is None:
                    amount = vault
                if amount <= 0 or amount > vault:
                    return {"status": "no_vault"}
                new_cash, new_vault = cash + amount, vault - amount
                t, src, delta = "VAULT_WITHDRAW", "withdraw_command", amount
            await conn.execute(
                "UPDATE wallets SET cash = $1, vault = $2, updated_at = now() "
                "WHERE bot_id = $3 AND user_id = $4",
                new_cash,
                new_vault,
                bid,
                uid,
            )
            await services.insert_transaction(
                conn,
                user_id=uid,
                type_=t,
                currency="cash",
                amount=delta,
                balance_before=cash,
                balance_after=new_cash,
                source=src,
                metadata={"vault_after": new_vault},
                bot_id=bid,
            )
            return {
                "status": "ok",
                "amount": amount,
                "cash": new_cash,
                "vault": new_vault,
                "cap": cap,
            }


async def _vault_move(message: Message, pool: asyncpg.Pool, deposit: bool, bot_id: int) -> None:
    if message.from_user is None:
        return
    u = await _prep(message, pool, bot_id=bot_id)
    cmd = "deposit" if deposit else "withdraw"
    ok, amount = _parse_amount(ax_common.cmd_args(message))
    if not ok:
        await message.reply(ui.error_card(f"Use /{cmd} [amount] or /{cmd} all"), parse_mode=ParseMode.HTML)
        return
    r = await _move(pool, u.id, amount, deposit, bot_id=bot_id)
    s = r["status"]
    if s == "ok":
        title = "DEPOSIT SECURED" if deposit else "WITHDRAWAL COMPLETE"
        icon = "🔐" if deposit else "💸"
        text = (
            f"🏦 <b>{title}</b>\n<blockquote>"
            f"{icon} <b>Amount:</b> <code>{CUR}{_n(r['amount'])}</code>\n"
            f"💰 <b>Liquid Cash:</b> <code>{CUR}{_n(r['cash'])}</code>\n"
            f"🔐 <b>Secured:</b> <code>{CUR}{_n(r['vault'])} / {CUR}{_n(r['cap'])}</code>"
            "</blockquote>"
        )
    elif s == "full":
        text = ui.error_card(f"Your vault is full ({CUR}{_n(r['cap'])} max limit).")
    elif s == "over_cap":
        text = ui.error_card(f"Your vault only has room for {CUR}{_n(r['room'])} more (20,000 max).")
    elif s == "no_cash":
        text = ui.error_card("You don't have that much cash to deposit.")
    elif s == "no_vault":
        text = ui.error_card("You don't have that much in your vault.")
    else:
        text = ui.error_card("Something went wrong. Please try again.")
    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("deposit"))
async def deposit_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await _vault_move(message, pool, True, bot_ctx.bot_id)


@router.message(Command("withdraw"))
async def withdraw_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await _vault_move(message, pool, False, bot_ctx.bot_id)


def _convert_card(
    brand_name: str,
    cash: int,
    gems: int,
    used_gems: int,
    uid: int,
) -> tuple[str, InlineKeyboardMarkup]:
    max_gems = getattr(config, "GEM_CONVERT_MAX_GEMS", 20)
    rate = getattr(config, "GEM_COIN_RATE", 10_000)
    left_quota = max(0, max_gems - used_gems)
    max_buy_by_cash = cash // rate
    can_buy = min(left_quota, max_buy_by_cash)

    text = (
        f"💎 <b>{ui.escape_html(brand_name)} GEM EXCHANGE</b>\n"
        "<blockquote>"
        f"💱 <b>Rate:</b> <code>{CUR}{_n(rate)} Coins = 1 💎 Gem</code>\n"
        f"📊 <b>Season Cap:</b> <code>{used_gems}/{max_gems} Gems</code> "
        f"(<code>{CUR}{_n(used_gems * rate)} / {CUR}{_n(max_gems * rate)}</code>)\n"
        f"💰 <b>Your Cash:</b> <code>{CUR}{_n(cash)}</code>\n"
        f"💎 <b>Your Gems:</b> <code>{_n(gems)}</code> (permanent)"
        "</blockquote>\n"
        "<i>Commands: <code>/convert gems 5</code> or <code>/convert coins 2</code></i>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💎 Buy 1 Gem ({CUR}{_n(rate)})",
                    callback_data=f"axconv:g:1:{uid}",
                ),
                InlineKeyboardButton(
                    text=f"💎 Buy Max ({can_buy})",
                    callback_data=f"axconv:g:max:{uid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🪙 Sell 1 Gem (+{CUR}{_n(rate)})",
                    callback_data=f"axconv:c:1:{uid}",
                ),
                InlineKeyboardButton(
                    text=f"🪙 Sell All Gems ({gems})",
                    callback_data=f"axconv:c:all:{uid}",
                ),
            ],
        ]
    )
    return text, kb


async def _do_convert_coins_to_gems(
    pool: asyncpg.Pool,
    uid: int,
    requested_gems: int | None,
    bot_id: int,
) -> dict:
    rate = getattr(config, "GEM_COIN_RATE", 10_000)
    cap_gems = getattr(config, "GEM_CONVERT_MAX_GEMS", 20)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_season_tables(conn)
            w = await conn.fetchrow(
                "SELECT cash, gems FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bot_id,
                uid,
            )
            if w is None:
                return {"status": "no_wallet"}
            conv_row = await conn.fetchrow(
                "SELECT converted_gems FROM ax_season_gem_conv WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bot_id,
                uid,
            )
            used = int(conv_row["converted_gems"]) if conv_row else 0
            left_quota = max(0, cap_gems - used)
            if left_quota <= 0:
                return {"status": "cap_reached", "used": used, "cap": cap_gems, "rate": rate}

            max_affordable = int(w["cash"]) // rate
            if max_affordable <= 0:
                return {"status": "no_cash", "have_cash": int(w["cash"]), "rate": rate}

            if requested_gems is None:
                gems_amt = min(left_quota, max_affordable)
            else:
                if requested_gems <= 0:
                    return {"status": "bad_amt"}
                if requested_gems > left_quota:
                    return {"status": "over_cap", "left": left_quota, "used": used, "cap": cap_gems}
                if requested_gems > max_affordable:
                    return {"status": "no_cash", "have_cash": int(w["cash"]), "rate": rate}
                gems_amt = requested_gems

            cost_coins = gems_amt * rate
            before_cash = int(w["cash"])
            after_cash = before_cash - cost_coins
            before_gems = int(w["gems"])
            after_gems = before_gems + gems_amt

            await conn.execute(
                "UPDATE wallets SET cash=$1, gems=$2, updated_at=now() "
                "WHERE bot_id=$3 AND user_id=$4",
                after_cash,
                after_gems,
                bot_id,
                uid,
            )
            await conn.execute(
                "INSERT INTO ax_season_gem_conv (bot_id, user_id, converted_gems, updated_at) "
                "VALUES ($1, $2, $3, now()) "
                "ON CONFLICT (bot_id, user_id) DO UPDATE SET "
                "converted_gems = ax_season_gem_conv.converted_gems + EXCLUDED.converted_gems, "
                "updated_at = now()",
                bot_id,
                uid,
                gems_amt,
            )
            await services.insert_transaction(
                conn,
                user_id=uid,
                type_="GEM_CONVERT_BUY",
                currency="cash",
                amount=-cost_coins,
                balance_before=before_cash,
                balance_after=after_cash,
                source="convert_command",
                metadata={"gems_gained": gems_amt, "rate": rate},
                bot_id=bot_id,
            )
            return {
                "status": "ok",
                "gems_delta": gems_amt,
                "coins_delta": -cost_coins,
                "cash": after_cash,
                "gems": after_gems,
                "used": used + gems_amt,
                "cap": cap_gems,
            }


async def _do_convert_gems_to_coins(
    pool: asyncpg.Pool,
    uid: int,
    requested_gems: int | None,
    bot_id: int,
) -> dict:
    rate = getattr(config, "GEM_COIN_RATE", 10_000)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_season_tables(conn)
            w = await conn.fetchrow(
                "SELECT cash, gems FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bot_id,
                uid,
            )
            if w is None:
                return {"status": "no_wallet"}
            have_gems = int(w["gems"])
            if have_gems <= 0:
                return {"status": "no_gems"}

            if requested_gems is None:
                gems_amt = have_gems
            else:
                if requested_gems <= 0:
                    return {"status": "bad_amt"}
                if requested_gems > have_gems:
                    return {"status": "not_enough_gems", "have_gems": have_gems}
                gems_amt = requested_gems

            gain_coins = gems_amt * rate
            before_cash = int(w["cash"])
            after_cash = before_cash + gain_coins
            after_gems = have_gems - gems_amt

            await conn.execute(
                "UPDATE wallets SET cash=$1, gems=$2, updated_at=now() "
                "WHERE bot_id=$3 AND user_id=$4",
                after_cash,
                after_gems,
                bot_id,
                uid,
            )
            await services.insert_transaction(
                conn,
                user_id=uid,
                type_="GEM_CONVERT_SELL",
                currency="cash",
                amount=gain_coins,
                balance_before=before_cash,
                balance_after=after_cash,
                source="convert_command",
                metadata={"gems_spent": gems_amt, "rate": rate},
                bot_id=bot_id,
            )
            conv_row = await conn.fetchrow(
                "SELECT converted_gems FROM ax_season_gem_conv WHERE bot_id=$1 AND user_id=$2",
                bot_id,
                uid,
            )
            used = int(conv_row["converted_gems"]) if conv_row else 0
            return {
                "status": "ok",
                "gems_delta": -gems_amt,
                "coins_delta": gain_coins,
                "cash": after_cash,
                "gems": after_gems,
                "used": used,
                "cap": getattr(config, "GEM_CONVERT_MAX_GEMS", 20),
            }


def _format_convert_result(res: dict, bname: str) -> str:
    st = res["status"]
    if st == "ok":
        if res["gems_delta"] > 0:
            action_line = (
                f"💎 <b>Converted:</b> <code>{CUR}{_n(abs(res['coins_delta']))}</code> → "
                f"<code>+{_n(res['gems_delta'])} Gems</code>"
            )
        else:
            action_line = (
                f"🪙 <b>Converted:</b> <code>{_n(abs(res['gems_delta']))} Gems</code> → "
                f"<code>+{CUR}{_n(res['coins_delta'])} Coins</code>"
            )
        return (
            f"✨ <b>EXCHANGE COMPLETE</b>\n<blockquote>"
            f"{action_line}\n"
            f"💰 <b>Cash:</b> <code>{CUR}{_n(res['cash'])}</code>\n"
            f"💎 <b>Gems:</b> <code>{_n(res['gems'])}</code>\n"
            f"📊 <b>Season Gem Cap Used:</b> <code>{res['used']}/{res['cap']}</code>"
            "</blockquote>"
        )
    if st == "no_wallet":
        return ui.error_card(f"Send /start to {bname} first 💜")
    if st == "cap_reached":
        return ui.error_card(
            f"Season gem conversion cap reached ({res['used']}/{res['cap']} Gems = {CUR}{_n(res['cap'] * res['rate'])})."
        )
    if st == "over_cap":
        return ui.error_card(
            f"Only {res['left']} Gems left in your season conversion cap ({res['used']}/{res['cap']} used)."
        )
    if st == "no_cash":
        return ui.error_card(
            f"Not enough cash. 1 Gem costs {CUR}{_n(res['rate'])} (you have {CUR}{_n(res['have_cash'])})."
        )
    if st == "no_gems":
        return ui.error_card("You have 0 Gems to convert.")
    if st == "not_enough_gems":
        return ui.error_card(f"You only have {res['have_gems']} Gems.")
    if st == "bad_amt":
        return ui.error_card("Enter a positive whole number.")
    return ui.error_card("Could not complete conversion.")


@router.message(Command("convert"))
async def convert_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    bname = get_brand(bid).name
    await _prep(message, pool, bot_id=bid)
    args = ax_common.cmd_args(message)

    async with pool.acquire() as conn:
        await _ensure_season_tables(conn)
        w = await services.get_wallet(pool, user.id, bot_id=bid)
        conv_row = await conn.fetchrow(
            "SELECT converted_gems FROM ax_season_gem_conv WHERE bot_id=$1 AND user_id=$2",
            bid,
            user.id,
        )

    cash = int(w["cash"]) if w else 0
    gems = int(w["gems"]) if w else 0
    used = int(conv_row["converted_gems"]) if conv_row else 0

    if not args:
        text, kb = _convert_card(bname, cash, gems, used, user.id)
        await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    direction = args[0].lower()
    qty: int | None
    if len(args) < 2 or args[1].lower() in ("max", "all"):
        qty = None
    else:
        raw_q = args[1].replace(",", "")
        if not raw_q.isdigit() or int(raw_q) <= 0:
            await message.reply(
                ui.error_card("Use: /convert gems [amount|max] or /convert coins [amount|all]"),
                parse_mode=ParseMode.HTML,
            )
            return
        qty = int(raw_q)

    if direction in ("gems", "gem", "g"):
        res = await _do_convert_coins_to_gems(pool, user.id, qty, bid)
    elif direction in ("coins", "coin", "cash", "c"):
        res = await _do_convert_gems_to_coins(pool, user.id, qty, bid)
    else:
        await message.reply(
            ui.error_card("Use: /convert gems [amount|max] or /convert coins [amount|all]"),
            parse_mode=ParseMode.HTML,
        )
        return

    await message.reply(_format_convert_result(res, bname), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("axconv:"))
async def convert_cb(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await query.answer()
        return
    _, direction, amt_raw, uid_str = parts
    uid = int(uid_str)
    if query.from_user.id != uid:
        await query.answer("This exchange menu is for someone else.", show_alert=True)
        return

    bid = bot_ctx.bot_id
    bname = get_brand(bid).name
    qty: int | None = None if amt_raw in ("max", "all") else int(amt_raw)
    if direction == "g":
        res = await _do_convert_coins_to_gems(pool, uid, qty, bid)
    elif direction == "c":
        res = await _do_convert_gems_to_coins(pool, uid, qty, bid)
    else:
        await query.answer()
        return

    msg_text = _format_convert_result(res, bname)
    await query.answer(
        "Converted ✅" if res["status"] == "ok" else "Check notice below",
        show_alert=res["status"] != "ok",
    )
    if query.message:
        try:
            await query.message.reply(msg_text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


@router.message(Command("toplegends"))
async def toplegends_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    bid = bot_ctx.bot_id
    bname = ui.escape_html(get_brand(bid).name)
    async with pool.acquire() as conn:
        await _ensure_season_tables(conn)
        latest_season = await conn.fetchval(
            "SELECT season_tag FROM ax_season_legends WHERE bot_id=$1 ORDER BY id DESC LIMIT 1",
            bid,
        )
        if not latest_season:
            await message.reply(
                f"🏆 <b>{bname} SEASON LEGENDS</b>\n"
                "<blockquote><i>No season legends have been recorded yet.</i></blockquote>",
                parse_mode=ParseMode.HTML,
            )
            return
        rows = await conn.fetch(
            "SELECT display_name, user_id, cash_snapshot, gems_snapshot, xp_snapshot, level_snapshot "
            "FROM ax_season_legends WHERE bot_id=$1 AND season_tag=$2 "
            "ORDER BY cash_snapshot DESC, xp_snapshot DESC LIMIT 10",
            bid,
            latest_season,
        )

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"<code>#{i + 1:02d}</code>"
        lines.append(
            f"{mark} <b>{ui.escape_html(r['display_name'])}</b> (LVL {r['level_snapshot']})\n"
            f"      💰 <code>{CUR}{_n(r['cash_snapshot'])}</code> • "
            f"💎 <code>{_n(r['gems_snapshot'])}</code> • "
            f"✨ <code>{_n(r['xp_snapshot'])} XP</code>"
        )

    await message.reply(
        f"🏆 <b>{bname} SEASON LEGENDS</b>\n"
        f"<i>Season: <code>{ui.escape_html(latest_season)}</code></i>\n\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>",
        parse_mode=ParseMode.HTML,
    )


async def record_mafia_result(pool: asyncpg.Pool, players, winners, bot_id: int | None = None) -> None:
    """players/winners: lists of objects with .user_id and .name"""
    bid = bot_context.resolve_bot_id(bot_id)
    win_ids = {p.user_id for p in winners}
    async with pool.acquire() as conn:
        async with conn.transaction():
            for p in sorted(players, key=lambda x: x.user_id):
                await conn.execute(
                    "INSERT INTO users (bot_id, id, first_name, created_at, updated_at) "
                    "VALUES ($1, $2, $3, now(), now()) ON CONFLICT (id) DO NOTHING",
                    bid,
                    p.user_id,
                    p.name,
                )
                won = 1 if p.user_id in win_ids else 0
                await conn.execute(
                    "INSERT INTO mafia_stats (bot_id, user_id, wins, games, updated_at) "
                    "VALUES ($1, $2, $3, 1, now()) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "bot_id = EXCLUDED.bot_id, "
                    "wins = mafia_stats.wins + EXCLUDED.wins, "
                    "games = mafia_stats.games + 1, updated_at = now()",
                    bid,
                    p.user_id,
                    won,
                )


@router.message(Command("mafiatop"))
async def mafiatop_command(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    bid = bot_ctx.bot_id
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT COALESCE(u.first_name, u.username, 'Player') AS name, s.wins "
            "FROM mafia_stats s JOIN users u ON u.id = s.user_id AND u.bot_id = s.bot_id "
            "WHERE s.bot_id = $1 AND s.wins > 0 ORDER BY s.wins DESC, s.games ASC LIMIT 10",
            bid,
        )
    if not rows:
        await message.reply(
            "🏆 <b>MAFIA: HALL OF FAME</b>\n<blockquote><i>No legends yet. Win a game to be the first!</i></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"<code>#{i + 1:02d}</code>"
        lines.append(f"{mark} <b>{ui.escape_html(r['name'])}</b> — <code>{r['wins']} Wins</code>")
    await message.reply(
        "🏆 <b>MAFIA: HALL OF FAME</b>\n<blockquote>"
        "<i>The most ruthless syndicates in the city.</i>\n" + "\n".join(lines) + "</blockquote>",
        parse_mode=ParseMode.HTML,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

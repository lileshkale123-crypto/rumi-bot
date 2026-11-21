"""/clan command and invite buttons (aiogram 3.x, per-bot isolated)."""
import time

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup as K, Message

import clan_admin as adm
import clan_services as svc
import clan_ui as ui
import config
from ax_common import cmd_args, resolve_target
from bot_context import BotContext
from services import ensure_user_and_wallet

router = Router(name="clans")
H = ParseMode.HTML
_invites: dict[tuple[int, int, int], float] = {}  # (bot_id, target_id, clan_id) -> expiry monotonic
_INVITES_MAX = 5000


def _prune_invites(now: float) -> None:
    if len(_invites) <= _INVITES_MAX:
        return
    expired = [k for k, exp in _invites.items() if exp < now]
    for k in expired:
        _invites.pop(k, None)
    if len(_invites) > _INVITES_MAX:
        oldest = sorted(_invites.items(), key=lambda kv: kv[1])[: _INVITES_MAX // 5]
        for k, _ in oldest:
            _invites.pop(k, None)


async def _say(msg: Message, text: str, kb: K | None = None):
    return await msg.reply(text, parse_mode=H, reply_markup=kb)


async def _target(message: Message, bot: Bot, pool: asyncpg.Pool, args: list[str], bot_id: int):
    t = await resolve_target(message, bot, pool, args=args, bot_id=bot_id)
    if t is None or getattr(t, "is_bot", False):
        return None
    await ensure_user_and_wallet(pool, t.id, getattr(t, "username", None), t.first_name, bot_id=bot_id)
    return t


@router.message(Command("clan"))
async def clan_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    u = message.from_user
    if u is None:
        return
    bid = bot_ctx.bot_id
    await ensure_user_and_wallet(pool, u.id, u.username, u.first_name, bot_id=bid)
    a = cmd_args(message)
    sub = a[0].lower() if a else "info"
    rest = a[1:]
    name = u.first_name or u.username or "Traveler"

    if sub == "create":
        r = await svc.create_clan(pool, u.id, rest[0] if rest else "", bot_id=bid)
        if r["status"] == "poor":
            await _say(
                message,
                f"⚠️ A clan costs {ui.S}{ui.n(r['need'])}. You have {ui.S}{ui.n(r['have'])}.",
            )
        else:
            await _say(
                message,
                ui.created(name, r["name"], r["fee"])
                if r["status"] == "ok"
                else ui.err(r["status"], bot_id=bid),
            )
    elif sub == "info":
        d = await svc.get_info(pool, u.id, bot_id=bid)
        await _say(message, ui.info(d) if d else ui.err("no_clan", bot_id=bid))
    elif sub == "deposit":
        raw = rest[0].replace(",", "") if rest else ""
        if not raw.isdigit():
            await _say(message, ui.err("bad_amount", bot_id=bid))
            return
        amt = int(raw)
        r = await svc.deposit(pool, u.id, amt, bot_id=bid)
        await _say(
            message,
            ui.deposit(name, amt, r["vault"], r["leveled_up"])
            if r["status"] == "ok"
            else ui.err(r["status"], bot_id=bid),
        )
    elif sub == "top":
        await _say(message, ui.top(await adm.top_clans(pool, bot_id=bid)))
    elif sub == "leave":
        r = await adm.leave_clan(pool, u.id, bot_id=bid)
        await _say(
            message,
            ui.simple("🚪 <b>YOU LEFT THE CLAN</b>", "Your bond is cut.")
            if r["status"] == "ok"
            else ui.err(r["status"], bot_id=bid),
        )
    elif sub == "disband":
        if not rest or rest[0].lower() != "confirm":
            await _say(
                message,
                "⚠️ This destroys your clan. Type <code>/clan disband confirm</code> to proceed.",
            )
            return
        r = await adm.disband(pool, u.id, bot_id=bid)
        await _say(
            message,
            ui.disbanded(r["name"], r["vault"], r["share"], r["members"])
            if r["status"] == "ok"
            else ui.err(r["status"], bot_id=bid),
        )
    elif sub in ("kick", "leader", "invite"):
        await _target_cmds(message, bot, pool, sub, rest, name, bid)
    else:
        await _say(
            message,
            "🏴 <b>CLAN COMMANDS</b>\n<blockquote>"
            "/clan create Name\n/clan info\n/clan deposit amount\n"
            "/clan invite (reply or @user)\n/clan leave\n/clan kick (reply or @user)\n"
            "/clan leader (reply or @user)\n/clan disband confirm\n/clan top</blockquote>",
        )


async def _target_cmds(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    sub: str,
    rest: list[str],
    name: str,
    bid: int,
) -> None:
    u = message.from_user
    if u is None:
        return
    t = await _target(message, bot, pool, rest, bid)
    if t is None:
        await _say(message, ui.err("no_target", bot_id=bid))
        return

    if sub == "invite":
        async with pool.acquire() as c:
            m = await svc.get_membership(c, u.id, bot_id=bid)
            tm = await c.fetchval(
                "SELECT 1 FROM clan_members WHERE bot_id=$1 AND user_id=$2",
                bid,
                t.id,
            )
        if m is None or m["role"] != "leader":
            await _say(message, ui.err("not_leader", bot_id=bid))
            return
        if tm:
            await _say(message, ui.err("their_clan", bot_id=bid))
            return
        now = time.monotonic()
        _prune_invites(now)
        _invites[(bid, t.id, m["id"])] = now + config.CLAN_INVITE_EXPIRE_MINUTES * 60
        kb = K(
            inline_keyboard=[
                [
                    B(text="✅ Accept", callback_data=f"clan:y:{m['id']}:{t.id}"),
                    B(text="❌ Decline", callback_data=f"clan:n:{m['id']}:{t.id}"),
                ]
            ]
        )
        await _say(message, ui.invite(name, m["name"], t.first_name or "friend"), kb)
        return

    fn = adm.kick_member if sub == "kick" else adm.transfer_leader
    r = await fn(pool, u.id, t.id, bot_id=bid)
    done = "👢 <b>MEMBER CAST OUT</b>" if sub == "kick" else "👑 <b>THE CROWN PASSES</b>"
    await _say(
        message,
        ui.simple(done, ui.e(t.first_name or "Member"))
        if r["status"] == "ok"
        else ui.err(r["status"], bot_id=bid),
    )


@router.callback_query(F.data.regexp(r"^clan:[yn]:\d+:\d+$"))
async def invite_cb(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    _, ans, cid_str, tid_str = (query.data or "").split(":")
    cid, tid = int(cid_str), int(tid_str)
    if query.from_user.id != tid:
        await query.answer("This offer is not for you.", show_alert=True)
        return
    bid = bot_ctx.bot_id
    exp = _invites.pop((bid, tid, cid), None)
    if ans == "n" or exp is None or exp < time.monotonic():
        await query.answer()
        txt = "🕯️ <b>The offer was declined.</b>" if ans == "n" else "⌛ <b>This offer has faded.</b>"
        if query.message:
            try:
                await query.message.edit_text(txt, parse_mode=H)
            except Exception:
                pass
        return

    await ensure_user_and_wallet(
        pool,
        query.from_user.id,
        query.from_user.username,
        query.from_user.first_name,
        bot_id=bid,
    )
    r = await svc.join_clan(pool, tid, cid, bot_id=bid)
    await query.answer()
    ok = ui.simple("🩸 <b>BLOOD PACT SEALED</b>", f"Welcome to <code>{ui.e(r.get('name', ''))}</code>.")
    if query.message:
        try:
            await query.message.edit_text(
                ok if r["status"] == "ok" else ui.err(r["status"], bot_id=bid),
                parse_mode=H,
            )
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

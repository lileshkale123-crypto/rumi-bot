"""/clan command and invite buttons."""
import time

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as K
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

import clan_admin as adm
import clan_services as svc
import clan_ui as ui
import config
from ax_common import resolve_target
from services import ensure_user_and_wallet

H = ParseMode.HTML
_invites = {}  # (target_id, clan_id) -> expiry


async def _say(msg, text, kb=None):
    await msg.reply_text(text, parse_mode=H, reply_markup=kb)


async def _target(update, context, pool):
    t = await resolve_target(update, context, pool)
    if t is None or getattr(t, "is_bot", False):
        return None
    await ensure_user_and_wallet(pool, t.id, getattr(t, "username", None), t.first_name)
    return t


async def clan_cmd(update, context):
    msg, u = update.effective_message, update.effective_user
    pool = context.bot_data["pool"]
    await ensure_user_and_wallet(pool, u.id, u.username, u.first_name)
    a = context.args or []
    sub = a[0].lower() if a else "info"
    rest = a[1:]
    name = u.first_name or u.username or "Traveler"

    if sub == "create":
        r = await svc.create_clan(pool, u.id, rest[0] if rest else "")
        if r["status"] == "poor":
            await _say(msg, f"⚠️ A clan costs {ui.S}{ui.n(r['need'])}. You have {ui.S}{ui.n(r['have'])}.")
        else:
            await _say(msg, ui.created(name, r["name"], r["fee"]) if r["status"] == "ok" else ui.err(r["status"]))
    elif sub == "info":
        d = await svc.get_info(pool, u.id)
        await _say(msg, ui.info(d) if d else ui.err("no_clan"))
    elif sub == "deposit":
        if not rest or not rest[0].isdigit():
            return await _say(msg, ui.err("bad_amount"))
        amt = int(rest[0])
        r = await svc.deposit(pool, u.id, amt)
        await _say(msg, ui.deposit(name, amt, r["vault"], r["leveled_up"]) if r["status"] == "ok" else ui.err(r["status"]))
    elif sub == "top":
        await _say(msg, ui.top(await adm.top_clans(pool)))
    elif sub == "leave":
        r = await adm.leave_clan(pool, u.id)
        await _say(msg, ui.simple("🚪 <b>YOU LEFT THE CLAN</b>", "Your bond is cut.") if r["status"] == "ok" else ui.err(r["status"]))
    elif sub == "disband":
        if not rest or rest[0].lower() != "confirm":
            return await _say(msg, "⚠️ This destroys your clan. Type <code>/clan disband confirm</code> to proceed.")
        r = await adm.disband(pool, u.id)
        await _say(msg, ui.disbanded(r["name"], r["vault"], r["share"], r["members"]) if r["status"] == "ok" else ui.err(r["status"]))
    elif sub in ("kick", "leader", "invite"):
        await _target_cmds(update, context, pool, sub, rest, name)
    else:
        await _say(msg, "🏴 <b>CLAN COMMANDS</b>\n<blockquote>/clan create Name\n/clan info\n/clan deposit amount\n/clan invite (reply or @user)\n/clan leave\n/clan kick (reply or @user)\n/clan leader (reply or @user)\n/clan disband confirm\n/clan top</blockquote>")


async def _target_cmds(update, context, pool, sub, rest, name):
    msg, u = update.effective_message, update.effective_user
    context.args = rest  # so resolve_target reads the name after the subcommand
    t = await _target(update, context, pool)
    if t is None:
        return await _say(msg, ui.err("no_target"))
    if sub == "invite":
        async with pool.acquire() as c:
            m = await svc.get_membership(c, u.id)
            tm = await c.fetchval("SELECT 1 FROM clan_members WHERE user_id=$1", t.id)
        if m is None or m["role"] != "leader":
            return await _say(msg, ui.err("not_leader"))
        if tm:
            return await _say(msg, ui.err("their_clan"))
        _invites[(t.id, m["id"])] = time.monotonic() + config.CLAN_INVITE_EXPIRE_MINUTES * 60
        kb = K([[B("✅ Accept", callback_data=f"clan:y:{m['id']}:{t.id}"), B("❌ Decline", callback_data=f"clan:n:{m['id']}:{t.id}")]])
        return await _say(msg, ui.invite(name, m["name"], t.first_name or "friend"), kb)
    fn = adm.kick_member if sub == "kick" else adm.transfer_leader
    r = await fn(pool, u.id, t.id)
    done = "👢 <b>MEMBER CAST OUT</b>" if sub == "kick" else "👑 <b>THE CROWN PASSES</b>"
    await _say(msg, ui.simple(done, ui.e(t.first_name or "Member")) if r["status"] == "ok" else ui.err(r["status"]))


async def invite_cb(update, context):
    q = update.callback_query
    _, ans, cid, tid = q.data.split(":")
    cid, tid = int(cid), int(tid)
    if q.from_user.id != tid:
        return await q.answer("This offer is not for you.", show_alert=True)
    exp = _invites.pop((tid, cid), None)
    if ans == "n" or exp is None or exp < time.monotonic():
        await q.answer()
        txt = "🕯️ <b>The offer was declined.</b>" if ans == "n" else "⌛ <b>This offer has faded.</b>"
        return await q.edit_message_text(txt, parse_mode=H)
    r = await svc.join_clan(context.bot_data["pool"], tid, cid)
    await q.answer()
    ok = ui.simple("🩸 <b>BLOOD PACT SEALED</b>", f"Welcome to <code>{ui.e(r.get('name', ''))}</code>.")
    await q.edit_message_text(ok if r["status"] == "ok" else ui.err(r["status"]), parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("clan", clan_cmd))
    app.add_handler(CallbackQueryHandler(invite_cb, pattern="^clan:[yn]:"))

"""Clan messages: dark-fantasy HTML cards."""
import html

import config

S = config.CURRENCY_SYMBOL
ERR = {
    "bad_name": f"Name must be {config.CLAN_NAME_MIN}-{config.CLAN_NAME_MAX} letters, numbers or _ (one word).",
    "name_taken": "That clan name is already taken.",
    "already_in_clan": "You are already in a clan.",
    "their_clan": "They already belong to a clan.",
    "poor": "You don't have enough cash for that.",
    "no_clan": "You are not in a clan. Use /clan create Name",
    "not_leader": "Only the clan leader can do that.",
    "leader_cannot_leave": "Leaders can't leave. Use /clan leader @user or /clan disband confirm.",
    "not_in_clan": "That person isn't in your clan.",
    "self": "You can't target yourself.",
    "bad_amount": "Enter a positive whole amount.",
    "full": "The clan is full.",
    "retry": "The clan changed while working. Please try again.",
    "gone": "That clan no longer exists.",
    "no_target": "Reply to their message or use @username.",
    "not_user": "That user hasn't used Rumi yet.",
}


def e(x):
    return html.escape(str(x))


def n(x):
    return f"{int(x):,}"


def err(status):
    return "⚠️ " + e(ERR.get(status, "Something went wrong. Please try again."))


def simple(title, body):
    return f"{title}\n<blockquote>{body}</blockquote>"


def created(leader, name, fee):
    return (
        "🏴 <b>CLAN FORGED</b>\n"
        "<blockquote>The underworld acknowledges your bloodline.\n"
        f"👑 <b>Leader:</b> {e(leader)}\n"
        f"🩸 <b>Clan:</b> <code>{e(name)}</code>\n"
        f"💸 <b>Tribute Paid:</b> <code>{S}{n(fee)}</code></blockquote>\n"
        "<i>Use /clan info to view your empire.</i>"
    )


def info(d):
    c = d["clan"]
    need = config.CLAN_LEVEL_REQUIREMENTS.get(c["level"] + 1)
    nxt = f"{S}{n(max(0, need - c['vault_balance']))} to go" if need else "MAX LEVEL"
    return (
        f"🏴 <b>{e(c['name'].upper())}</b>\n"
        "<blockquote>"
        f"👑 <b>Leader:</b> {e(d['leader'])}\n"
        f"📈 <b>Level:</b> <code>{c['level']}</code>\n"
        f"👥 <b>Members:</b> <code>{d['members']}/{config.CLAN_MAX_MEMBERS}</code>\n"
        f"💰 <b>Shared Vault:</b> <code>{S}{n(c['vault_balance'])}</code>\n"
        f"⭐ <b>Next Rank:</b> <code>{nxt}</code></blockquote>\n"
        "<i>Active Buffs: Level up the vault to unlock underworld perks.</i>"
    )


def deposit(name, amount, vault, leveled):
    t = (
        "💰 <b>VAULT DEPOSIT SECURED</b>\n"
        "<blockquote>"
        f"👤 <b>Member:</b> {e(name)}\n"
        f"💵 <b>Amount Transferred:</b> <code>+{S}{n(amount)}</code>\n"
        f"🏦 <b>New Vault Total:</b> <code>{S}{n(vault)}</code></blockquote>"
    )
    return t + ("\n\n<i>🔥 The clan ascends. A new level is reached!</i>" if leveled else "")


def invite(inviter, clan, target):
    return simple(
        "🕯️ <b>A BLOOD PACT IS OFFERED</b>",
        f"<b>{e(inviter)}</b> invites <b>{e(target)}</b> to join "
        f"<code>{e(clan)}</code>.\nOnly {e(target)} can answer. The offer fades in "
        f"{config.CLAN_INVITE_EXPIRE_MINUTES} minutes.",
    )


def disbanded(name, vault, share, members):
    return simple(
        "💀 <b>CLAN DISBANDED</b>",
        f"<code>{e(name)}</code> is no more.\n"
        f"🏦 <b>Vault:</b> <code>{S}{n(vault)}</code> split between {members} member(s)\n"
        f"💵 <b>Each received:</b> <code>{S}{n(share)}</code>",
    )


def top(rows):
    if not rows:
        return "🏴 <b>TOP CLANS</b>\n\nNo clans have risen yet."
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        m = medals[i] if i < 3 else f"<code>#{i + 1:02d}</code>"
        lines.append(
            f"{m} <b>{e(r['name'])}</b> • Lv {r['level']} • {r['members']} members\n"
            f"      <code>{S}{n(r['vault_balance'])}</code>"
        )
    return "🏴 <b>TOP CLANS</b>\n<i>Ranked by vault</i>\n\n<blockquote>" + "\n".join(lines) + "</blockquote>"

"""
Message templates and inline keyboards for Rumi.

Style: bold + blockquote HTML, cute-premium tone. All dynamic text (names)
must go through escape_html() before being embedded in a template.
"""

import html
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config


def escape_html(text: str) -> str:
    return html.escape(str(text))


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _time_remaining(target: datetime) -> str:
    now = datetime.now(timezone.utc)
    remaining = target - now
    total_seconds = max(0, int(remaining.total_seconds()))
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

def start_card(first_name: str, wallet, command_list: list[tuple[str, str]]) -> str:
    name = escape_html(first_name)
    cash = wallet["cash"] if wallet else 0
    gems = wallet["gems"] if wallet else 0
    level = wallet["level"] if wallet else 1
    xp = wallet["xp"] if wallet else 0

    command_lines = "\n".join(f"🔷 <b>{cmd}</b> — {desc}" for cmd, desc in command_list)

    return (
        f"<b>Welcome, {name}</b>\n"
        f"<i>Rumi</i>\n\n"
        f"🌙 <b>Your Snapshot</b>\n"
        f"💰 Cash: {config.CURRENCY_SYMBOL}{format_number(cash)}\n"
        f"💎 Gems: {gems}\n"
        f"⭐ Level: {level}\n"
        f"✨ XP: {xp}\n\n"
        f"🎮 <b>Available Commands</b>\n"
        f"{command_lines}\n\n"
        f"<b>Tip:</b> tap a button below to get started."
    )


def start_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌟 Features", callback_data="features")],
        [
            InlineKeyboardButton("👥 Community", url="https://t.me/lordzenithx"),
            InlineKeyboardButton("🎮 Games", callback_data="games"),
        ],
        [InlineKeyboardButton("📣 Updates", url="https://t.me/ZENITHREIGNS")],
        [InlineKeyboardButton("➕ Add Rumi to your group", url=f"https://t.me/{bot_username}?startgroup=true")],
    ])


# ---------------------------------------------------------------------------
# /wallet (balance)
# ---------------------------------------------------------------------------

def wallet_card(name: str, wallet) -> str:
    n = escape_html(name)
    shield_line = ""
    if False:  # shield status is hidden from the wallet card
        shield_line = f"\n🛡️ <i>Shield active — {_time_remaining(wallet['shield_until'])} remaining</i>"

    return (
        f"💳 <b>RUMI WALLET</b>\n"
        f"👤 <b>{n}</b> <code>LVL {wallet['level']}</code>\n\n"
        f"<blockquote>"
        f"💰 <b>Cash:</b> <code>{config.CURRENCY_SYMBOL}{format_number(wallet['cash'])}</code>\n"
        f"💎 <b>Gems:</b> <code>{wallet['gems']}</code>\n"
        f"✨ <b>XP:</b> <code>{wallet['xp']}</code>"
        f"</blockquote>"
        f"{shield_line}"
    )


def wallet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎁 Claim", callback_data="claim"),
            InlineKeyboardButton("🏆 Rankings", callback_data="rankings:cash:1"),
        ],
        [InlineKeyboardButton("🔄 Refresh", callback_data="wallet:refresh")],
    ])


# ---------------------------------------------------------------------------
# /claim (daily reward)
# ---------------------------------------------------------------------------

def reward_claimed_card(cash_reward: int, gem_reward: int, lucky_bonus: int, new_streak: int,
                          milestone_hit: int | None, is_weekly_bonus: bool) -> str:
    lines = ["🎁 <b>REWARD CLAIMED</b>", ""]
    reward_line = f"✨ <b>Reward:</b> <code>+{config.CURRENCY_SYMBOL}{format_number(cash_reward)} Coins</code>"
    if gem_reward > 0:
        reward_line += f" • <code>+{gem_reward} Gems</code>"
    lines.append(f"<blockquote>{reward_line}")
    lines.append(f"🔥 <b>Streak:</b> <code>Day {new_streak}</code></blockquote>")

    if is_weekly_bonus:
        lines.append("")
        lines.append(f"<i>🎉 7-day streak bonus! Next bonus at Day {new_streak + config.DAILY_WEEKLY_BONUS_INTERVAL}.</i>")
    elif lucky_bonus > 0:
        lines.append("")
        lines.append(f"<i>✨ Lucky bonus included: +{config.CURRENCY_SYMBOL}{format_number(lucky_bonus)}</i>")

    if milestone_hit:
        lines.append("")
        lines.append(f"<i>🏅 Milestone reached: Day {milestone_hit}!</i>")

    return "\n".join(lines)


def reward_cooldown_card(next_claim_at: datetime) -> str:
    return (
        f"⏳ <b>Reward already claimed today</b>\n"
        f"Come back in {_time_remaining(next_claim_at)}."
    )


# ---------------------------------------------------------------------------
# /pay
# ---------------------------------------------------------------------------

def payment_success_card(amount: int, to_name: str, new_balance: int, tax: int = 0, received: int | None = None) -> str:
    name = escape_html(to_name)
    tax_line = ""
    if tax > 0:
        tax_line = f"\n🧾 <i>Tax {config.CURRENCY_SYMBOL}{format_number(tax)} • {name} received {config.CURRENCY_SYMBOL}{format_number(received)}</i>"
    return (
        f"🌙 <b>Payment Sent!</b>\n\n"
        f"💸 <b>{config.CURRENCY_SYMBOL}{format_number(amount)}</b> → {name}\n\n"
        f"<blockquote>💳 New balance: {config.CURRENCY_SYMBOL}{format_number(new_balance)}</blockquote>"
        f"{tax_line}"
    )

# ---------------------------------------------------------------------------
# /guard (shield)
# ---------------------------------------------------------------------------

def guard_success_card(shield_until: datetime) -> str:
    return (
        f"🛡️ <b>GUARD RAISED</b>\n\n"
        f"<blockquote>"
        f"🔒 <b>Protection:</b> <code>{config.SHIELD_DURATION_HOURS} Hours</code>\n"
        f"💸 <b>Fee Paid:</b> <code>{config.CURRENCY_SYMBOL}{config.SHIELD_COST}</code>"
        f"</blockquote>\n\n"
        f"<i>Your Wallet is safe from robbery attempts until then.</i>"
    )


def guard_fail_card(required: int, available: int) -> str:
    return (
        f"⚠️ <b>GUARD FAILED</b>\n\n"
        f"<blockquote>"
        f"❌ <b>Required:</b> <code>{config.CURRENCY_SYMBOL}{required}</code>\n"
        f"💼 <b>Available:</b> <code>{config.CURRENCY_SYMBOL}{available}</code>"
        f"</blockquote>"
    )


# ---------------------------------------------------------------------------
# /rob
# ---------------------------------------------------------------------------

def rob_success_card(amount: int, victim_name: str, tax: int = 0, net: int | None = None) -> str:
    tax_line = ""
    if tax > 0:
        tax_line = f"\n🧾 <b>Tax:</b> <code>-{config.CURRENCY_SYMBOL}{format_number(tax)}</code> • <b>You keep:</b> <code>{config.CURRENCY_SYMBOL}{format_number(net)}</code>"
    name = escape_html(victim_name)
    return (
        f"🎭 <b>HEIST SUCCESSFUL</b>\n\n"
        f"<blockquote>💰 <b>Snatched:</b> <code>{config.CURRENCY_SYMBOL}{format_number(amount)}</code> from {name}"
        f"{tax_line}</blockquote>"
    )


def rob_fail_card(penalty: int, victim_name: str) -> str:
    name = escape_html(victim_name)
    if penalty > 0:
        return (
            f"😼 <b>HEIST FAILED</b>\n\n"
            f"<blockquote>💸 You were caught and paid <code>{config.CURRENCY_SYMBOL}{format_number(penalty)}</code> to {name}</blockquote>"
        )
    return f"😼 <b>HEIST FAILED</b>\n\nBetter luck next time."


def rob_cooldown_card(retry_at: datetime) -> str:
    return f"⏳ <b>Still recovering from your last heist</b>\nTry again in {_time_remaining(retry_at)}."


def rob_victim_shielded_card() -> str:
    return "🛡️ <b>Target is protected by a Guard</b> — heist blocked."


def rob_victim_too_poor_card() -> str:
    return f"💼 <b>Target doesn't have enough to make it worth it</b> (needs at least {config.CURRENCY_SYMBOL}{config.ROB_MIN_VICTIM_BALANCE})."


# ---------------------------------------------------------------------------
# /rankings (leaderboard)
# ---------------------------------------------------------------------------

def leaderboard_card(title: str, emoji: str, entries: list, page: int, total_pages: int, is_currency: bool) -> str:
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 <b>RANKINGS</b> • {emoji} {title}", ""]
    body_lines = []
    for i, entry in enumerate(entries):
        rank = (page - 1) * config.LEADERBOARD_PAGE_SIZE + i + 1
        medal = medals[rank - 1] if rank <= 3 else f"<code>#{rank:02d}</code>"
        value = f"{config.CURRENCY_SYMBOL}{format_number(entry['value'])}" if is_currency else format_number(entry["value"])
        body_lines.append(f"{medal} <b>{escape_html(entry['name'])}</b> <code>{value}</code>")

    if body_lines:
        lines.append(f"<blockquote>{chr(10).join(body_lines)}</blockquote>")
    else:
        lines.append("Nobody here yet — be the first!")

    lines.append("")
    lines.append(f"<i>Page {page} of {total_pages}</i>")
    return "\n".join(lines)


def leaderboard_keyboard(category: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("💵 Richest", callback_data="rankings:cash:1"),
        InlineKeyboardButton("⭐ XP", callback_data="rankings:xp:1"),
        InlineKeyboardButton("💎 Gems", callback_data="rankings:gems:1"),
    ]]
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀", callback_data=f"rankings:{category}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("▶", callback_data=f"rankings:{category}:{page + 1}"))
    rows.append(nav_row)
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Generic error
# ---------------------------------------------------------------------------

def error_card(message: str) -> str:
    return f"⚠️ {escape_html(message)}"


# ---------------------------------------------------------------------------
# /guard when a shield is already active
# ---------------------------------------------------------------------------

def guard_already_active_card(shield_until: datetime) -> str:
    return (
        f"🛡️ <b>ALREADY PROTECTED</b>\n\n"
        f"<blockquote>"
        f"⏳ <b>Time left:</b> <code>{_time_remaining(shield_until)}</code>"
        f"</blockquote>\n\n"
        f"<i>No charge. You can raise a new Guard once this one ends.</i>"
    )

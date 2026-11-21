import html
from datetime import datetime, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import bot_context, config
from ax_common import btn

def escape_html(text: str) -> str:
    return html.escape(str(text))

def format_number(n: int) -> str:
    return f"{n:,}"

def _time_remaining(target: datetime) -> str:
    return "24h 0m"

def start_card(first_name: str, wallet, command_list, bot_id=None) -> str:
    return f"<b>{escape_html(first_name)}</b>"

def start_keyboard(bot_username: str, bot_id=None):
    return InlineKeyboardMarkup(inline_keyboard=[])

def wallet_card(name: str, wallet, full: bool = False, bot_id=None) -> str:
    return f"<b>{escape_html(name)}</b>"

def wallet_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[])

def reward_claimed_card(cash_reward: int, gem_reward: int, lucky_bonus: int, new_streak: int, milestone_hit: int | None, is_weekly_bonus: bool, xp_info=None) -> str:
    return f"+{cash_reward}"

def reward_cooldown_card(next_claim_at: datetime) -> str:
    return "Cooldown"

def payment_success_card(amount: int, to_name: str, new_balance: int, tax: int = 0, received=None) -> str:
    return f"{amount}"

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
    return f"{required}/{available}"

def rob_success_card(amount: int, victim_name: str, tax: int = 0, net=None) -> str:
    return f"{amount}"

def rob_fail_card(penalty: int, victim_name: str) -> str:
    return f"{penalty}"

def rob_cooldown_card(retry_at: datetime) -> str:
    return "Cooldown"

def rob_victim_shielded_card() -> str:
    return "Shielded"

def rob_victim_too_poor_card() -> str:
    return "Poor"

def leaderboard_card(title: str, emoji: str, entries: list, page: int, total_pages: int, is_currency: bool) -> str:
    lines = [f"🏆 <b>RANKINGS</b> • {emoji} {title}", ""]
    medals = ["🥇", "🥈", "🥉"]
    body_lines = []
    for i, entry in enumerate(entries):
        rank = (page - 1) * config.LEADERBOARD_PAGE_SIZE + i + 1
        medal = medals[rank - 1] if rank <= 3 else f"<code>#{rank:02d}</code>"
        value = f"{config.CURRENCY_SYMBOL}{format_number(entry['value'])}" if is_currency else format_number(entry["value"])
        title_suffix = f" · {escape_html(entry['title'])}" if entry.get("title") else ""
        body_lines.append(f"{medal} <b>{escape_html(entry['name'])}</b>{title_suffix} <code>{value}</code>")
    lines.append("\n".join(body_lines))
    return "\n".join(lines)

def leaderboard_keyboard(category: str, page: int, total_pages: int):
    return InlineKeyboardMarkup(inline_keyboard=[])

def error_card(message: str) -> str:
    return escape_html(message)

def guard_already_active_card(shield_until: datetime) -> str:
    return "Active"

def games_card() -> str:
    return "Games"

def xp_line(xp_info) -> str:
    return ""

def wallet_card_premium(name: str, wallet, emoji: str = "", full: bool = False, bot_id=None) -> str:
    brand = bot_context.get_brand(bot_id)
    n = escape_html(name)
    return (
        f"💎 <b>{escape_html(brand.upper_name)} PREMIUM WALLET</b>\n\n"
        f"{emoji}<b>{n}</b>  <code>LVL {wallet['level']}</code>\n\n"
        "<blockquote>"
        f"💰 <b>Cash:</b> <code>{config.CURRENCY_SYMBOL}{(wallet['cash'] if full else format_number(wallet['cash']))}</code>\n\n"
        f"💎 <b>Gems:</b> <code>{wallet['gems']}</code>\n\n"
        f"✨ <b>XP:</b> <code>{wallet['xp']}</code>"
        "</blockquote>"
    )

"""Shadow Bluff cards and keyboards (text and buttons only) (aiogram 3.x)."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from ax_common import btn
from ui import escape_html as esc, format_number as fmt

S = config.CURRENCY_SYMBOL


def _prize(wager: int, count: int) -> int:
    pot = wager * count
    return pot - int(pot * config.BLUFF_TAX_PERCENT)


def lobby_card(host: str, wager: int, names: list[str], max_players: int) -> str:
    lines = "\n".join(f"• {esc(n)}" for n in names)
    return (
        "🃏 <b>SHADOW BLUFF: LOBBY</b>\n"
        f"<blockquote>👑 <b>Monarch:</b> {esc(host)}\n"
        f"💰 <b>Wager:</b> <code>{S}{fmt(wager)}</code>\n"
        f"🏆 <b>Pot:</b> <code>{S}{fmt(_prize(wager, len(names)))}</code>\n"
        f"👥 <b>Contenders:</b> <code>{len(names)}/{max_players}</code>\n"
        f"{lines}</blockquote>\n"
        "⏳ <i>The shadows wait for challengers...</i>"
    )


def lobby_keyboard(game_id: str, wager: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🃏 Join ({S}{fmt(wager)})", callback_data=f"bluff:join:{game_id}")],
            [
                InlineKeyboardButton(text="▶️ Start", callback_data=f"bluff:start:{game_id}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"bluff:cancel:{game_id}"),
            ],
        ]
    )


def round_card(round_no: int, rows: list[tuple[str, bool]], seconds: int) -> str:
    lines = "\n".join(
        f"• <b>{esc(n)}:</b> " + ("✅ <i>Card Locked</i>" if done else "⏳ <i>Plotting...</i>")
        for n, done in rows
    )
    return (
        f"🃏 <b>SHADOW BLUFF: ROUND {round_no}/4</b>\n"
        f"<blockquote>🎴 <b>THE BOARD:</b>\n{lines}</blockquote>\n"
        f"⏳ <i>{seconds} seconds remaining to cast your hand.</i>"
    )


def round_keyboard(game_id: str, letters: list[str], styled: bool = True) -> InlineKeyboardMarkup:
    # Green button uses Telegram's newer button style. If rejected, the handler retries with styled=False.
    view = btn(
        "👁️ VIEW YOUR HAND",
        data=f"bluff:view:{game_id}",
        style="success" if styled else None,
    )
    cards = [
        InlineKeyboardButton(text=f"[ {L} ]", callback_data=f"bluff:play:{game_id}:{L}")
        for L in letters
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[view], cards])


def results_card(round_no: int, plays: list[tuple[str, int]], winner_names: list[str], points: int) -> str:
    lines = "\n".join(f"• <b>{esc(n)}:</b> Played <code>{v}</code>" for n, v in plays)
    who = " & ".join(f"<b>{esc(n)}</b>" for n in winner_names)
    verb = "claims" if len(winner_names) == 1 else "each claim"
    return (
        f"🎯 <b>ROUND {round_no} RESULTS</b>\n"
        f"<blockquote>{lines}\n"
        f"🏆 {who} {verb} the board and take <code>{points} Points</code>!</blockquote>"
    )


def final_card(
    ranking: list[tuple[str, int]],
    winners: list[tuple[str, int, int]],
    hands: list[tuple[str, dict[str, int]]],
) -> str:
    medals = ["🥇", "🥈", "🥉"]
    win_names = {w[0] for w in winners}
    label = "WINNER" if len(winners) == 1 else "WINNERS"
    rank_lines = "\n".join(
        f"{medals[i] if i < 3 else '▫️'} <b>{esc(n)}:</b> <code>{p} Points</code>"
        + (" \u2014 <b>WINNER</b>" if n in win_names else "")
        for i, (n, p) in enumerate(ranking)
    )
    win_lines = "\n".join(
        f"💰 <b>{esc(n)}</b> claimed <code>+{S}{fmt(gain)}</code> • 💼 <code>{S}{fmt(bal)}</code>"
        for n, gain, bal in winners
    )
    hand_lines = "\n".join(
        f"• {esc(n)}: " + " ".join(f"{k}{v}" for k, v in h.items()) + f" (total {sum(h.values())})"
        for n, h in hands
    )
    return (
        "👑 <b>SHADOW BLUFF: MATCH CONCLUDED</b>\n"
        f"<blockquote>\U0001F3C6 <b>{label}:</b> {', '.join(esc(w[0]) for w in winners)}\n"
        f"{rank_lines}\n{win_lines}</blockquote>\n"
        f"🎴 <b>Starting hands</b>\n<blockquote>{hand_lines}</blockquote>"
    )

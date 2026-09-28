"""Shadow Bluff cards and keyboards (text and buttons only)."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
from ui import escape_html as esc, format_number as fmt

S = config.CURRENCY_SYMBOL


def _prize(wager, count):
    pot = wager * count
    return pot - int(pot * config.BLUFF_TAX_PERCENT)


def lobby_card(host, wager, names, max_players):
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


def lobby_keyboard(game_id, wager):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🃏 Join ({S}{fmt(wager)})", callback_data=f"bluff:join:{game_id}")],
        [InlineKeyboardButton("▶️ Start", callback_data=f"bluff:start:{game_id}"),
         InlineKeyboardButton("❌ Cancel", callback_data=f"bluff:cancel:{game_id}")],
    ])


def round_card(round_no, rows, seconds):
    lines = "\n".join(
        f"• <b>{esc(n)}:</b> " + ("✅ <i>Card Locked</i>" if done else "⏳ <i>Plotting...</i>")
        for n, done in rows
    )
    return (
        f"🃏 <b>SHADOW BLUFF: ROUND {round_no}/4</b>\n"
        f"<blockquote>🎴 <b>THE BOARD:</b>\n{lines}</blockquote>\n"
        f"⏳ <i>{seconds} seconds remaining to cast your hand.</i>"
    )


def round_keyboard(game_id, letters, styled=True):
    # Green button uses Telegram's newer button style. If it is rejected, the handler retries with styled=False.
    kw = {"api_kwargs": {"style": "success"}} if styled else {}
    view = InlineKeyboardButton("👁️ VIEW YOUR HAND", callback_data=f"bluff:view:{game_id}", **kw)
    cards = [InlineKeyboardButton(f"[ {L} ]", callback_data=f"bluff:play:{game_id}:{L}") for L in letters]
    return InlineKeyboardMarkup([[view], cards])


def results_card(round_no, plays, winner_names, points):
    lines = "\n".join(f"• <b>{esc(n)}:</b> Played <code>{v}</code>" for n, v in plays)
    who = " & ".join(f"<b>{esc(n)}</b>" for n in winner_names)
    verb = "claims" if len(winner_names) == 1 else "each claim"
    return (
        f"🎯 <b>ROUND {round_no} RESULTS</b>\n"
        f"<blockquote>{lines}\n"
        f"🏆 {who} {verb} the board and take <code>{points} Points</code>!</blockquote>"
    )


def final_card(ranking, winners, hands):
    medals = ["🥇", "🥈", "🥉"]
    rank_lines = "\n".join(
        f"{medals[i] if i < 3 else '▫️'} <b>{esc(n)}:</b> <code>{p} Points</code>"
        for i, (n, p) in enumerate(ranking)
    )
    win_lines = "\n".join(
        f"💰 <b>{esc(n)}</b> claimed <code>+{S}{fmt(gain)}</code> • 💼 <code>{S}{fmt(bal)}</code>"
        for n, gain, bal in winners
    )
    win_names = [w[0] for w in winners]
    label = "WINNER" if len(win_names) == 1 else "WINNERS"
    rank_lines = "\n".join(
        line + (" \u2014 <b>WINNER</b>" if ranking[i][0] in win_names else "")
        for i, line in enumerate(rank_lines.split("\n"))
    )
    hand_lines = "\n".join(
        f"• {esc(n)}: " + " ".join(f"{k}{v}" for k, v in h.items()) + f" (total {sum(h.values())})"
        for n, h in hands
    )
    return (
        "👑 <b>SHADOW BLUFF: MATCH CONCLUDED</b>\n"
        f"<blockquote>\U0001F3C6 <b>{label}:</b> {', '.join(esc(n) for n in win_names)}\n{rank_lines}\n{win_lines}</blockquote>\n"
        f"🎴 <b>Starting hands</b>\n<blockquote>{hand_lines}</blockquote>"
    )

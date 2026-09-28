"""Shadow Bluff game logic: hands, escrow, refunds, payouts."""
import json
import random
import uuid

import config
from services import insert_transaction

CARD_LETTERS = ["A", "B", "C", "D"]


def make_hands(player_count: int):
    """Every player gets 4 cards (1-10) that add up to the SAME total."""
    target = random.randint(config.BLUFF_HAND_SUM_MIN, config.BLUFF_HAND_SUM_MAX)
    hands = []
    while len(hands) < player_count:
        cards = [random.randint(1, 10) for _ in range(4)]
        if sum(cards) == target:
            hands.append(dict(zip(CARD_LETTERS, cards)))
    return hands, target


async def _take_buyin(conn, user_id, wager, game_id):
    """Locks the wallet, checks rules, deducts the wager. Returns None if OK, else an error string."""
    wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user_id)
    if wallet is None:
        return "no_wallet"
    busy = await conn.fetchrow(
        "SELECT id FROM bluff_games WHERE status IN ('lobby','active') AND player_ids @> $1::jsonb",
        json.dumps([user_id]),
    )
    if busy:
        return "already_playing"
    if wallet["cash"] < wager:
        return "insufficient_funds"
    await conn.execute(
        "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE user_id = $2", wager, user_id
    )
    await insert_transaction(
        conn, user_id=user_id, type_="BLUFF_BUYIN", currency="cash", amount=-wager,
        balance_before=wallet["cash"], balance_after=wallet["cash"] - wager,
        source="bluff", metadata={"game_id": game_id},
        idempotency_key=f"bluff:{game_id}:buyin:{user_id}",
    )
    return None


async def create_game(pool, chat_id, host_id, wager):
    if wager < config.BLUFF_MIN_WAGER:
        return {"status": "wager_too_low"}
    if wager > config.BLUFF_MAX_WAGER:
        return {"status": "wager_too_high"}
    game_id = uuid.uuid4().hex[:10]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", chat_id)
            busy = await conn.fetchrow(
                "SELECT id FROM bluff_games WHERE chat_id = $1 AND status IN ('lobby','active')", chat_id
            )
            if busy:
                return {"status": "chat_busy"}
            err = await _take_buyin(conn, host_id, wager, game_id)
            if err:
                return {"status": err}
            await conn.execute(
                "INSERT INTO bluff_games (id, chat_id, wager, status, player_ids) VALUES ($1, $2, $3, 'lobby', $4::jsonb)",
                game_id, chat_id, wager, json.dumps([host_id]),
            )
    return {"status": "ok", "game_id": game_id}


async def join_game(pool, game_id, user_id):
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow("SELECT * FROM bluff_games WHERE id = $1 FOR UPDATE", game_id)
            if game is None or game["status"] != "lobby":
                return {"status": "closed"}
            players = json.loads(game["player_ids"])
            if user_id in players:
                return {"status": "already_joined"}
            if len(players) >= config.BLUFF_MAX_PLAYERS:
                return {"status": "full"}
            err = await _take_buyin(conn, user_id, game["wager"], game_id)
            if err:
                return {"status": err}
            players.append(user_id)
            await conn.execute(
                "UPDATE bluff_games SET player_ids = $1::jsonb WHERE id = $2", json.dumps(players), game_id
            )
    return {"status": "ok", "players": players}


async def _refund_locked(conn, game):
    """Refund every player. Call with the game row already locked, inside a transaction."""
    players = json.loads(game["player_ids"])
    for uid in sorted(players):
        w = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", uid)
        await conn.execute(
            "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE user_id = $2", game["wager"], uid
        )
        await insert_transaction(
            conn, user_id=uid, type_="BLUFF_REFUND", currency="cash", amount=game["wager"],
            balance_before=w["cash"], balance_after=w["cash"] + game["wager"],
            source="bluff", metadata={"game_id": game["id"]},
            idempotency_key=f"bluff:{game['id']}:refund:{uid}",
        )
    await conn.execute("UPDATE bluff_games SET status = 'cancelled' WHERE id = $1", game["id"])


async def refund_game(pool, game_id):
    """Used for timeouts and crashes. Returns True if a refund happened."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow("SELECT * FROM bluff_games WHERE id = $1 FOR UPDATE", game_id)
            if game is None or game["status"] not in ("lobby", "active"):
                return False
            await _refund_locked(conn, game)
            return True


async def cancel_lobby(pool, game_id, user_id):
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow("SELECT * FROM bluff_games WHERE id = $1 FOR UPDATE", game_id)
            if game is None or game["status"] != "lobby":
                return {"status": "closed"}
            if json.loads(game["player_ids"])[0] != user_id:
                return {"status": "not_host"}
            await _refund_locked(conn, game)
            return {"status": "ok"}


async def start_game(pool, game_id, user_id):
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow("SELECT * FROM bluff_games WHERE id = $1 FOR UPDATE", game_id)
            if game is None or game["status"] != "lobby":
                return {"status": "closed"}
            players = json.loads(game["player_ids"])
            if players[0] != user_id:
                return {"status": "not_host"}
            if len(players) < config.BLUFF_MIN_PLAYERS:
                return {"status": "not_enough"}
            await conn.execute("UPDATE bluff_games SET status = 'active' WHERE id = $1", game_id)
            return {"status": "ok", "players": players}


async def finish_game(pool, game_id, scores):
    """scores = {user_id: points}. Pays the pot (minus tax) to the top scorer(s), split on ties."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow("SELECT * FROM bluff_games WHERE id = $1 FOR UPDATE", game_id)
            if game is None or game["status"] != "active":
                return {"status": "closed"}
            players = json.loads(game["player_ids"])
            pot = game["wager"] * len(players)
            tax = int(pot * config.BLUFF_TAX_PERCENT)
            prize = pot - tax
            best = max(scores.values())
            winners = [u for u in players if scores[u] == best]
            share, extra = divmod(prize, len(winners))
            balances = {}
            for uid in sorted(winners):
                w = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", uid)
                gain = share + (extra if uid == winners[0] else 0)
                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, wins = wins + 1, updated_at = now() WHERE user_id = $2",
                    gain, uid,
                )
                await insert_transaction(
                    conn, user_id=uid, type_="BLUFF_WIN", currency="cash", amount=gain,
                    balance_before=w["cash"], balance_after=w["cash"] + gain,
                    source="bluff", metadata={"game_id": game_id, "points": best, "tax": tax},
                    idempotency_key=f"bluff:{game_id}:win:{uid}",
                )
                balances[uid] = w["cash"] + gain
            await conn.execute("UPDATE bluff_games SET status = 'finished' WHERE id = $1", game_id)
            return {"status": "ok", "winners": winners, "prize": prize, "share": share,
                    "pot": pot, "tax": tax, "balances": balances}


async def refund_unfinished(pool):
    """Run once at startup: refunds any game that was left open by a restart."""
    async with pool.acquire() as conn:
        ids = [r["id"] for r in await conn.fetch("SELECT id FROM bluff_games WHERE status IN ('lobby','active')")]
    count = 0
    for gid in ids:
        if await refund_game(pool, gid):
            count += 1
    return count


async def mark_active(pool, game_id):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE bluff_games SET status = 'active' WHERE id = $1 AND status = 'lobby'", game_id)

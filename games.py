"""Shadow Bluff game logic: hands, escrow, refunds, payouts (bot_id aware)."""
import json
import random
import uuid

import asyncpg

import bot_context
import config
from services import insert_transaction

CARD_LETTERS = ["A", "B", "C", "D"]


def _parse_players(raw) -> list[int]:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw or [])


def make_hands(player_count: int) -> tuple[list[dict[str, int]], int]:
    """Every player gets 4 cards (1-10) that add up to the SAME total."""
    target = random.randint(config.BLUFF_HAND_SUM_MIN, config.BLUFF_HAND_SUM_MAX)
    hands = []
    while len(hands) < player_count:
        cards = [random.randint(1, 10) for _ in range(4)]
        if sum(cards) == target:
            hands.append(dict(zip(CARD_LETTERS, cards)))
    return hands, target


async def _take_buyin(conn, user_id: int, wager: int, game_id: str, bot_id: int) -> str | None:
    """Locks the wallet, checks rules, deducts the wager. Returns None if OK, else an error string."""
    wallet = await conn.fetchrow(
        "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
        bot_id,
        user_id,
    )
    if wallet is None:
        return "no_wallet"
    busy = await conn.fetchrow(
        "SELECT id FROM bluff_games WHERE bot_id = $1 AND status IN ('lobby','active') AND player_ids @> $2::jsonb",
        bot_id,
        json.dumps([user_id]),
    )
    if busy:
        return "already_playing"
    if wallet["cash"] < wager:
        return "insufficient_funds"
    await conn.execute(
        "UPDATE wallets SET cash = cash - $1, updated_at = now() WHERE bot_id = $2 AND user_id = $3",
        wager,
        bot_id,
        user_id,
    )
    await insert_transaction(
        conn,
        user_id=user_id,
        type_="BLUFF_BUYIN",
        currency="cash",
        amount=-wager,
        balance_before=wallet["cash"],
        balance_after=wallet["cash"] - wager,
        source="bluff",
        metadata={"game_id": game_id},
        idempotency_key=f"bluff:{bot_id}:{game_id}:buyin:{user_id}",
        bot_id=bot_id,
    )
    return None


async def create_game(
    pool: asyncpg.Pool,
    chat_id: int,
    host_id: int,
    wager: int,
    bot_id: int | None = None,
) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    if wager < config.BLUFF_MIN_WAGER:
        return {"status": "wager_too_low"}
    if wager > config.BLUFF_MAX_WAGER:
        return {"status": "wager_too_high"}
    game_id = uuid.uuid4().hex[:10]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", chat_id)
            busy = await conn.fetchrow(
                "SELECT id FROM bluff_games WHERE bot_id = $1 AND chat_id = $2 AND status IN ('lobby','active')",
                bid,
                chat_id,
            )
            if busy:
                return {"status": "chat_busy"}
            err = await _take_buyin(conn, host_id, wager, game_id, bid)
            if err:
                return {"status": err}
            await conn.execute(
                "INSERT INTO bluff_games (bot_id, id, chat_id, wager, status, player_ids) "
                "VALUES ($1, $2, $3, $4, 'lobby', $5::jsonb)",
                bid,
                game_id,
                chat_id,
                wager,
                json.dumps([host_id]),
            )
    return {"status": "ok", "game_id": game_id}


async def join_game(
    pool: asyncpg.Pool,
    game_id: str,
    user_id: int,
    bot_id: int | None = None,
) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow(
                "SELECT * FROM bluff_games WHERE bot_id = $1 AND id = $2 FOR UPDATE",
                bid,
                game_id,
            )
            if game is None or game["status"] != "lobby":
                return {"status": "closed"}
            players = _parse_players(game["player_ids"])
            if user_id in players:
                return {"status": "already_joined"}
            if len(players) >= config.BLUFF_MAX_PLAYERS:
                return {"status": "full"}
            err = await _take_buyin(conn, user_id, game["wager"], game_id, bid)
            if err:
                return {"status": err}
            players.append(user_id)
            await conn.execute(
                "UPDATE bluff_games SET player_ids = $1::jsonb WHERE bot_id = $2 AND id = $3",
                json.dumps(players),
                bid,
                game_id,
            )
    return {"status": "ok", "players": players}


async def _refund_locked(conn, game) -> None:
    """Refund every player. Call with the game row already locked, inside a transaction."""
    bid = game["bot_id"] or bot_context.get_main_context().bot_id
    players = _parse_players(game["player_ids"])
    for uid in sorted(players):
        w = await conn.fetchrow(
            "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
            bid,
            uid,
        )
        if w is None:
            continue
        await conn.execute(
            "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id = $2 AND user_id = $3",
            game["wager"],
            bid,
            uid,
        )
        await insert_transaction(
            conn,
            user_id=uid,
            type_="BLUFF_REFUND",
            currency="cash",
            amount=game["wager"],
            balance_before=w["cash"],
            balance_after=w["cash"] + game["wager"],
            source="bluff",
            metadata={"game_id": game["id"]},
            idempotency_key=f"bluff:{bid}:{game['id']}:refund:{uid}",
            bot_id=bid,
        )
    await conn.execute(
        "UPDATE bluff_games SET status = 'cancelled' WHERE bot_id = $1 AND id = $2",
        bid,
        game["id"],
    )


async def refund_game(pool: asyncpg.Pool, game_id: str, bot_id: int | None = None) -> bool:
    """Used for timeouts and crashes. Returns True if a refund happened."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            if bot_id is not None:
                game = await conn.fetchrow(
                    "SELECT * FROM bluff_games WHERE bot_id = $1 AND id = $2 FOR UPDATE",
                    bot_id,
                    game_id,
                )
            else:
                game = await conn.fetchrow(
                    "SELECT * FROM bluff_games WHERE id = $1 FOR UPDATE",
                    game_id,
                )
            if game is None or game["status"] not in ("lobby", "active"):
                return False
            await _refund_locked(conn, game)
            return True


async def cancel_lobby(
    pool: asyncpg.Pool,
    game_id: str,
    user_id: int,
    bot_id: int | None = None,
) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow(
                "SELECT * FROM bluff_games WHERE bot_id = $1 AND id = $2 FOR UPDATE",
                bid,
                game_id,
            )
            if game is None or game["status"] != "lobby":
                return {"status": "closed"}
            players = _parse_players(game["player_ids"])
            if not players or players[0] != user_id:
                return {"status": "not_host"}
            await _refund_locked(conn, game)
            return {"status": "ok"}


async def start_game(
    pool: asyncpg.Pool,
    game_id: str,
    user_id: int,
    bot_id: int | None = None,
) -> dict:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow(
                "SELECT * FROM bluff_games WHERE bot_id = $1 AND id = $2 FOR UPDATE",
                bid,
                game_id,
            )
            if game is None or game["status"] != "lobby":
                return {"status": "closed"}
            players = _parse_players(game["player_ids"])
            if not players or players[0] != user_id:
                return {"status": "not_host"}
            if len(players) < config.BLUFF_MIN_PLAYERS:
                return {"status": "not_enough"}
            await conn.execute(
                "UPDATE bluff_games SET status = 'active' WHERE bot_id = $1 AND id = $2",
                bid,
                game_id,
            )
            return {"status": "ok", "players": players}


async def finish_game(
    pool: asyncpg.Pool,
    game_id: str,
    scores: dict[int, int],
    bot_id: int | None = None,
) -> dict:
    """scores = {user_id: points}. Pays the pot (minus tax) to the top scorer(s), split on ties."""
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow(
                "SELECT * FROM bluff_games WHERE bot_id = $1 AND id = $2 FOR UPDATE",
                bid,
                game_id,
            )
            if game is None or game["status"] != "active":
                return {"status": "closed"}
            players = _parse_players(game["player_ids"])
            if not players or not scores:
                return {"status": "closed"}
            pot = game["wager"] * len(players)
            tax = int(pot * config.BLUFF_TAX_PERCENT)
            prize = pot - tax
            await conn.fetch(
                "SELECT user_id FROM wallets WHERE bot_id = $1 AND user_id = ANY($2::bigint[]) "
                "ORDER BY user_id FOR UPDATE",
                bid,
                [int(p) for p in players],
            )
            best = max(scores.get(u, 0) for u in players)
            winners = [u for u in players if scores.get(u, 0) == best]
            share, extra = divmod(prize, len(winners))
            balances = {}
            for uid in sorted(winners):
                w = await conn.fetchrow(
                    "SELECT * FROM wallets WHERE bot_id = $1 AND user_id = $2 FOR UPDATE",
                    bid,
                    uid,
                )
                if w is None:
                    continue
                gain = share + (extra if uid == winners[0] else 0)
                await conn.execute(
                    "UPDATE wallets SET cash = cash + $1, wins = wins + 1, updated_at = now() "
                    "WHERE bot_id = $2 AND user_id = $3",
                    gain,
                    bid,
                    uid,
                )
                await insert_transaction(
                    conn,
                    user_id=uid,
                    type_="BLUFF_WIN",
                    currency="cash",
                    amount=gain,
                    balance_before=w["cash"],
                    balance_after=w["cash"] + gain,
                    source="bluff",
                    metadata={"game_id": game_id, "points": best, "tax": tax},
                    idempotency_key=f"bluff:{bid}:{game_id}:win:{uid}",
                    bot_id=bid,
                )
                balances[uid] = w["cash"] + gain

            import services

            xp_map = {}
            for uid in sorted(players):
                amt = config.XP_BLUFF_PLAY + (config.XP_BLUFF_WIN if uid in winners else 0)
                xp_map[uid] = await services.award_xp(conn, uid, amt, bot_id=bid)

            await conn.execute(
                "UPDATE bluff_games SET status = 'finished' WHERE bot_id = $1 AND id = $2",
                bid,
                game_id,
            )
            return {
                "status": "ok",
                "winners": winners,
                "prize": prize,
                "share": share,
                "pot": pot,
                "tax": tax,
                "balances": balances,
                "xp": xp_map,
            }


async def refund_unfinished(pool: asyncpg.Pool, bot_id: int | None = None) -> int:
    """Run once at startup: refunds any game that was left open by a restart."""
    async with pool.acquire() as conn:
        if bot_id is not None:
            rows = await conn.fetch(
                "SELECT id, bot_id FROM bluff_games WHERE bot_id = $1 AND status IN ('lobby','active')",
                bot_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, bot_id FROM bluff_games WHERE status IN ('lobby','active')"
            )
    count = 0
    for r in rows:
        if await refund_game(pool, r["id"], bot_id=r["bot_id"]):
            count += 1
    return count


async def mark_active(pool: asyncpg.Pool, game_id: str, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE bluff_games SET status = 'active' WHERE bot_id = $1 AND id = $2 AND status = 'lobby'",
            bid,
            game_id,
        )

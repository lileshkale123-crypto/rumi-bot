import asyncio
import os
import sys
import urllib.parse as up

import asyncpg

APPLY = "--apply" in sys.argv

EXTRA_DDL = [
    "CREATE TABLE IF NOT EXISTS ax_premium (user_id BIGINT PRIMARY KEY, emoji_id TEXT, "
    "emoji_char TEXT, since TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS ax_ref_bonus (new_user_id BIGINT PRIMARY KEY, "
    "referrer_id BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "bonus_paid BOOLEAN NOT NULL DEFAULT FALSE)",
    "CREATE TABLE IF NOT EXISTS ax_donations (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, "
    "first_name TEXT, stars INT NOT NULL, charge_id TEXT, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS ax_group_rewards (chat_id BIGINT PRIMARY KEY, "
    "user_id BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
]

SUM_TABLES = {
    "wallets": (["user_id"], ["cash", "gems", "xp", "vault", "wins", "losses"],
                ["shield_until", "last_work_at", "last_spin_at", "last_topup_at", "updated_at"], []),
    "ax_user_stats": (["user_id"], ["message_count"], ["last_seen"], ["first_seen"]),
    "mafia_stats": (["user_id"], ["wins", "games"], ["updated_at"], []),
    "streaks": (["user_id"], [], ["current_streak", "longest_streak", "last_claim_date"], []),
    "ax_gift_inv": (["user_id", "item_id"], ["qty"], [], []),
    "ax_gift_recv": (["user_id", "item_id"], ["qty"], [], []),
    "bounties": (["target_id"], ["bounty_amount"], [], []),
}

SKIP = {
    "bluff_games", "ax_captcha_pending", "ax_chan_cleanup", "ax_bc_msgs", "ax_bc_batches",
    "rob_cooldowns", "rob_xp_log", "rob_msg_log", "ax_kill_status", "merge_done",
}

SERIAL_ID = {"transactions", "ai_memory", "ax_warn_log", "ax_watch_log", "ax_donations"}

HANDLED_SPECIALLY = {"users", "clans", "clan_members"} | set(SUM_TABLES)


def read_env(path):
    vals = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


async def connect(url):
    u = up.urlparse(url)
    conn = await asyncpg.connect(
        user=up.unquote(u.username or ""),
        password=up.unquote(u.password or ""),
        host=u.hostname,
        port=u.port or 5432,
        database=(u.path or "/postgres")[1:] or "postgres",
        ssl="require",
        statement_cache_size=0,
        timeout=30,
    )
    return conn, u.hostname


async def tables_of(conn):
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'")
    return {r["table_name"] for r in rows}


async def columns_of(conn, t):
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=$1 AND is_generated='NEVER' "
        "ORDER BY ordinal_position", t)
    return [r["column_name"] for r in rows]


async def count(conn, t):
    return await conn.fetchval(f'SELECT count(*) FROM "{t}"')


async def run_many(new, sql, data):
    for i in range(0, len(data), 500):
        await new.executemany(sql, data[i:i + 500])


async def copy_plain(old, new, t, report):
    ocols, ncols = await columns_of(old, t), set(await columns_of(new, t))
    cols = [c for c in ocols if c in ncols and not (t in SERIAL_ID and c == "id")]
    rows = await old.fetch(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}"')
    before = await count(new, t)
    if rows:
        cl = ", ".join(f'"{c}"' for c in cols)
        ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
        sql = f'INSERT INTO "{t}" ({cl}) VALUES ({ph}) ON CONFLICT DO NOTHING'
        await run_many(new, sql, [tuple(r[c] for c in cols) for r in rows])
    report.append((t, len(rows), before, await count(new, t), "copied"))


async def merge_sum(old, new, t, spec, report):
    keys, adds, maxs, mins = spec
    ocols, ncols = await columns_of(old, t), set(await columns_of(new, t))
    cols = [c for c in ocols if c in ncols]
    adds = [c for c in adds if c in cols]
    maxs = [c for c in maxs if c in cols]
    mins = [c for c in mins if c in cols]
    rows = await old.fetch(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}"')
    before = await count(new, t)
    sets = [f'"{c}" = "{t}"."{c}" + excluded."{c}"' for c in adds]
    sets += [f'"{c}" = GREATEST("{t}"."{c}", excluded."{c}")' for c in maxs]
    sets += [f'"{c}" = LEAST("{t}"."{c}", excluded."{c}")' for c in mins]
    conflict = (f'ON CONFLICT ({", ".join(chr(34) + k + chr(34) for k in keys)}) DO UPDATE SET '
                + ", ".join(sets)) if sets else "ON CONFLICT DO NOTHING"
    if rows:
        cl = ", ".join(f'"{c}"' for c in cols)
        ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
        sql = f'INSERT INTO "{t}" ({cl}) VALUES ({ph}) {conflict}'
        await run_many(new, sql, [tuple(r[c] for c in cols) for r in rows])
    report.append((t, len(rows), before, await count(new, t), "added together"))
    return [r["user_id"] for r in rows] if "user_id" in cols else []


async def merge_clans(old, new, report):
    existing = {r["name"].lower(): r["id"] for r in await new.fetch("SELECT id, name FROM clans")}
    before_c, before_m = await count(new, "clans"), await count(new, "clan_members")
    cmap = {}
    oc = await old.fetch("SELECT * FROM clans ORDER BY id")
    for c in oc:
        key = c["name"].lower()
        if key in existing:
            nid = existing[key]
            await new.execute(
                "UPDATE clans SET vault_balance = vault_balance + $1 WHERE id = $2",
                c["vault_balance"], nid)
            print(f"   note: clan '{c['name']}' exists in both -> merged into one (vaults added)")
        else:
            nid = await new.fetchval(
                "INSERT INTO clans (name, leader_id, level, vault_balance, created_at) "
                "VALUES ($1,$2,$3,$4,$5) RETURNING id",
                c["name"], c["leader_id"], c["level"], c["vault_balance"], c["created_at"])
            existing[key] = nid
        cmap[c["id"]] = nid
    om = await old.fetch("SELECT * FROM clan_members")
    for m in om:
        nid = cmap.get(m["clan_id"])
        if nid is None:
            continue
        await new.execute(
            "INSERT INTO clan_members (user_id, clan_id, role, joined_at) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (user_id) DO NOTHING",
            m["user_id"], nid, m["role"], m["joined_at"])
    report.append(("clans", len(oc), before_c, await count(new, "clans"), "copied (ids remapped)"))
    report.append(("clan_members", len(om), before_m, await count(new, "clan_members"), "copied"))


async def main():
    if not (os.path.exists(".env") and os.path.exists(".env.bak_neon") and os.path.exists("schema.sql")):
        sys.exit("Run this inside the rumi-python folder (needs .env, .env.bak_neon, schema.sql).")
    new_url = read_env(".env")["DATABASE_URL"]
    old_url = read_env(".env.bak_neon")["DATABASE_URL"]

    print("Connecting...")
    new, new_host = await connect(new_url)
    old, old_host = await connect(old_url)
    print("  NEW database host:", new_host)
    print("  OLD database host:", old_host)
    if new_url == old_url:
        sys.exit("STOP: .env and .env.bak_neon point to the same database.")

    print("MODE:", "APPLY (will be saved)" if APPLY else "DRY RUN (will be undone at the end)")
    report, overlap = [], []
    tx = new.transaction()
    await tx.start()
    try:
        with open("schema.sql", encoding="utf-8") as f:
            await new.execute(f.read())
        for ddl in EXTRA_DDL:
            await new.execute(ddl)

        await new.execute(
            "CREATE TABLE IF NOT EXISTS merge_done (id INT PRIMARY KEY, at TIMESTAMPTZ DEFAULT now())")
        if await new.fetchval("SELECT count(*) FROM merge_done") > 0:
            raise SystemExit("STOP: the merge was already done once. Running again would double the coins.")

        old_tables, new_tables = await tables_of(old), await tables_of(new)

        for t in SUM_TABLES:
            if t in old_tables:
                await new.execute(f'CREATE TABLE IF NOT EXISTS "{t}_premerge_bak" AS SELECT * FROM "{t}"')

        new_cash_before = await new.fetchval("SELECT COALESCE(SUM(cash),0) FROM wallets")
        old_cash = await old.fetchval("SELECT COALESCE(SUM(cash),0) FROM wallets")
        new_w = {r["user_id"]: r["cash"] for r in await new.fetch("SELECT user_id, cash FROM wallets")}
        old_w = {r["user_id"]: r["cash"] for r in await old.fetch("SELECT user_id, cash FROM wallets")}
        both = [u for u in old_w if u in new_w]
        overlap = sorted(both, key=lambda u: -old_w[u])[:5]
        before_vals = {u: (new_w[u], old_w[u]) for u in overlap}

        await copy_plain(old, new, "users", report)

        if "clans" in old_tables and "clan_members" in old_tables:
            await merge_clans(old, new, report)

        touched = []
        for t, spec in SUM_TABLES.items():
            if t in old_tables:
                ids = await merge_sum(old, new, t, spec, report)
                if t == "wallets":
                    touched = ids
        if touched:
            await new.execute(
                "UPDATE wallets SET level = GREATEST(level, 1 + FLOOR(SQRT(xp / 100.0))::int) "
                "WHERE user_id = ANY($1::bigint[])", touched)

        for t in sorted(old_tables - HANDLED_SPECIALLY - SKIP):
            if t.endswith("_premerge_bak"):
                continue
            if t in new_tables:
                await copy_plain(old, new, t, report)
            else:
                print(f"   note: table '{t}' is only in the old DB and not used by the bot -> skipped")

        await new.execute("INSERT INTO merge_done (id) VALUES (1)")

        print("\n{:<24}{:>8}{:>10}{:>10}  what".format("table", "old", "new-before", "new-after"))
        for t, o, b, a, how in report:
            print(f"{t:<24}{o:>8}{b:>10}{a:>10}  {how}")

        new_cash_after = await new.fetchval("SELECT COALESCE(SUM(cash),0) FROM wallets")
        print("\nTOTAL COINS  new before:", new_cash_before, "| old:", old_cash,
              "| after merge:", new_cash_after)
        print("Expected after merge:", new_cash_before + old_cash,
              "->", "OK" if new_cash_after == new_cash_before + old_cash else "MISMATCH!")
        print("Users in both DBs:", len(both), "| only in old DB:", len(set(old_w) - set(new_w)))
        if overlap:
            print("\nExamples (user id: new + old = after):")
            for u in overlap:
                after = await new.fetchval("SELECT cash FROM wallets WHERE user_id=$1", u)
                print(f"  {u}: {before_vals[u][0]} + {before_vals[u][1]} = {after}")

        if new_cash_after != new_cash_before + old_cash:
            raise SystemExit("STOP: totals do not match, nothing was saved.")
        if APPLY:
            await tx.commit()
            print("\nDONE. Merge saved. Restart your bot now.")
        else:
            await tx.rollback()
            print("\nDRY RUN finished. NOTHING was changed. If the numbers look right, run:")
            print("    python merge_db.py --apply")
    except BaseException:
        try:
            await tx.rollback()
        except Exception:
            pass
        print("\nFAILED - nothing was changed in your new database.")
        raise
    finally:
        await new.close()
        await old.close()


asyncio.run(main())

"""Quick CLI helper to inspect a user's wallet from .env DATABASE_URL."""
import asyncio
import sys
import urllib.parse as up

import asyncpg


def env(p: str) -> str | None:
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


async def look(p: str, name: str) -> None:
    dsn = env(p)
    if not dsn:
        print(p, "-> DATABASE_URL not found")
        return
    u = up.urlparse(dsn)
    c = await asyncpg.connect(
        user=up.unquote(u.username or ""),
        password=up.unquote(u.password or ""),
        host=u.hostname,
        port=u.port or 5432,
        database=u.path[1:],
        ssl="require",
        statement_cache_size=0,
        timeout=30,
    )
    try:
        print(p, "->", u.hostname)
        rows = await c.fetch(
            "select u.id,u.username,u.first_name,w.cash,w.gems,w.xp "
            "from users u left join wallets w on w.user_id=u.id "
            "where u.first_name ilike $1 or u.username ilike $1",
            "%" + name + "%",
        )
        for r in rows:
            print("  ", dict(r))
        if not rows:
            print("   no match")
        print(
            "   users:",
            await c.fetchval("select count(*) from users"),
            "| total cash:",
            await c.fetchval("select coalesce(sum(cash),0) from wallets"),
        )
    finally:
        await c.close()


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python who.py <name_or_username>")
        return
    for p in (".env", ".env.bak_neon"):
        try:
            await look(p, sys.argv[1])
        except Exception as e:
            print(p, "FAILED:", e)


if __name__ == "__main__":
    asyncio.run(main())

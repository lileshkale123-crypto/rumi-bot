import asyncio, sys, urllib.parse as up, asyncpg
def env(p):
    for l in open(p):
        if l.startswith("DATABASE_URL="):
            return l.split("=",1)[1].strip().strip('"').strip("'")
async def look(p, name):
    u = up.urlparse(env(p))
    c = await asyncpg.connect(user=up.unquote(u.username), password=up.unquote(u.password), host=u.hostname, port=u.port or 5432, database=u.path[1:], ssl="require", statement_cache_size=0, timeout=30)
    print(p, "->", u.hostname)
    rows = await c.fetch("select u.id,u.username,u.first_name,w.cash,w.gems,w.xp from users u left join wallets w on w.user_id=u.id where u.first_name ilike $1 or u.username ilike $1", "%"+name+"%")
    for r in rows: print("  ", dict(r))
    if not rows: print("   no match")
    print("   users:", await c.fetchval("select count(*) from users"), "| total cash:", await c.fetchval("select coalesce(sum(cash),0) from wallets"))
    await c.close()
async def main():
    for p in (".env", ".env.bak_neon"):
        try: await look(p, sys.argv[1])
        except Exception as e: print(p, "FAILED:", e)
asyncio.run(main())

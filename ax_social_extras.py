"""Friends & tagging (/addf, /tag, /t, /ctag, /delf, /allf, /friend, /friends, /all, @all), /setintro, /intro & /detail (aiogram 3.x)."""
import asyncio, html, json, logging, time
import asyncpg
from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from ax_adminpanel import OWNER_IDS
from ax_common import cmd_arg_text, cmd_args, fire, resolve_target
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_social_extras")
H = ParseMode.HTML
_all_cd: dict[tuple[int, int], float] = {}

async def _ensure_tables(conn):
    await conn.execute("CREATE TABLE IF NOT EXISTS ax_user_friends (bot_id BIGINT NOT NULL DEFAULT 0, owner_id BIGINT NOT NULL, friend_id BIGINT NOT NULL, tag_name TEXT NOT NULL, added_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, owner_id, friend_id))")
    await conn.execute("CREATE TABLE IF NOT EXISTS ax_user_intros (bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, intro_text TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id))")
    await conn.execute("CREATE TABLE IF NOT EXISTS ax_name_history (bot_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, names_json TEXT NOT NULL DEFAULT '[]', usernames_json TEXT NOT NULL DEFAULT '[]', updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id))")

def _push_last4(items: list[str], value: str | None) -> list[str]:
    clean = (value or "").strip()
    if not clean:
        return items[-4:]
    out = [x for x in items if x != clean]
    out.append(clean)
    return out[-4:]

@router.message(Command("addf"))
async def addf_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    args = cmd_args(message)
    t = await resolve_target(message, bot, pool, args=args, bot_id=bot_ctx.bot_id)
    tag = (args[1] if (not message.reply_to_message and len(args) > 1) else (args[0] if args else "")).lower()[:32]
    if not t or not tag:
        await message.reply("Use: <code>/addf &lt;tag&gt;</code> (reply) or <code>/addf @user &lt;tag&gt;</code>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await _ensure_tables(c)
        await c.execute("INSERT INTO ax_user_friends (bot_id, owner_id, friend_id, tag_name) VALUES ($1,$2,$3,$4) ON CONFLICT (bot_id, owner_id, friend_id) DO UPDATE SET tag_name=EXCLUDED.tag_name", bot_ctx.bot_id, message.from_user.id, t.id, tag)
    await message.reply(f"💖 Saved <b>{html.escape(t.first_name or 'Friend')}</b> as <code>{html.escape(tag)}</code>.", parse_mode=H)

@router.message(Command("tag", "t"))
async def tag_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    tags = [a.lower() for a in cmd_args(message)]
    if not tags:
        await message.reply("Use: <code>/tag name1 name2</code> or <code>/t name1</code>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await _ensure_tables(c)
        rows = await c.fetch("SELECT f.friend_id, f.tag_name, COALESCE(u.first_name,'Friend') AS name FROM ax_user_friends f LEFT JOIN users u ON u.id=f.friend_id WHERE f.bot_id=$1 AND f.owner_id=$2 AND LOWER(f.tag_name)=ANY($3::text[]) LIMIT 30", bot_ctx.bot_id, message.from_user.id, tags)
    if not rows:
        await message.reply("No matching friends.")
        return
    mentions = " ".join(f'<a href="tg://user?id={r["friend_id"]}">{html.escape(r["name"])}</a>' for r in rows)
    await message.reply(f"📣 {mentions}", parse_mode=H)

@router.message(Command("ctag", "delf", "allf", "friend", "friends"))
async def friend_misc_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
    bid, uid = bot_ctx.bot_id, message.from_user.id
    args = cmd_args(message)
    by_reply = bool(message.reply_to_message and message.reply_to_message.from_user)
    async with pool.acquire() as c:
        await _ensure_tables(c)
        if cmd in ("allf", "friends"):
            rows = await c.fetch(
                "SELECT f.tag_name, COALESCE(u.first_name,'Friend') AS name FROM ax_user_friends f "
                "LEFT JOIN users u ON u.id=f.friend_id WHERE f.bot_id=$1 AND f.owner_id=$2 "
                "ORDER BY f.tag_name, name LIMIT 100", bid, uid)
            if not rows:
                await message.reply("👥 No friends yet. Reply to someone with <code>/addf gang</code> to save them.", parse_mode=H)
                return
            groups: dict[str, list[str]] = {}
            for r in rows:
                groups.setdefault(r["tag_name"], []).append(html.escape(r["name"]))
            body = "\n".join(f"🏷 <b>{html.escape(t)}</b>: {', '.join(n)}" for t, n in groups.items())
            await message.reply(f"👥 <b>Your friends</b> ({len(rows)})\n<blockquote>{body}</blockquote>", parse_mode=H)
        elif cmd == "friend":
            cnt = await c.fetchval("SELECT COUNT(*)::int FROM ax_user_friends WHERE bot_id=$1 AND owner_id=$2", bid, uid)
            await message.reply(f"👥 Friends count: <b>{cnt}</b>", parse_mode=H)
        elif cmd == "delf":
            first = args[0] if args else ""
            if by_reply or first.startswith("@") or first.lstrip("-").isdigit():
                if not by_reply and first.lstrip("-").isdigit():
                    fid, fname = int(first), first
                else:
                    t = await resolve_target(message, bot, pool, args=args, bot_id=bid)
                    if not t:
                        await message.reply("💫 I couldn't find that person.")
                        return
                    fid, fname = t.id, (t.first_name or "Friend")
                res = await c.execute("DELETE FROM ax_user_friends WHERE bot_id=$1 AND owner_id=$2 AND friend_id=$3", bid, uid, fid)
                if res.split()[-1] != "0":
                    await message.reply(f"🗑 Removed <b>{html.escape(str(fname))}</b> from your friends.", parse_mode=H)
                else:
                    await message.reply("They are not in your friends list.")
            elif first:
                tag = first.lower()[:32]
                res = await c.execute("DELETE FROM ax_user_friends WHERE bot_id=$1 AND owner_id=$2 AND LOWER(tag_name)=$3", bid, uid, tag)
                n = int(res.split()[-1])
                await message.reply(f"🗑 Removed <b>{n}</b> friend(s) tagged <code>{html.escape(tag)}</code>." if n else "No friends with that tag.", parse_mode=H)
            else:
                await message.reply("Use: <code>/delf</code> (reply to a friend), <code>/delf @user</code> or <code>/delf tag</code> to remove a whole tag.", parse_mode=H)
        else:  # ctag
            if by_reply and args:
                tag = args[0].lower()[:32]
                t = message.reply_to_message.from_user
                res = await c.execute("UPDATE ax_user_friends SET tag_name=$4 WHERE bot_id=$1 AND owner_id=$2 AND friend_id=$3", bid, uid, t.id, tag)
                ok = res.split()[-1] != "0"
                await message.reply(f"🏷 <b>{html.escape(t.first_name or 'Friend')}</b> is now tagged <code>{html.escape(tag)}</code>." if ok else "They are not in your friends list. Use /addf first.", parse_mode=H)
            elif len(args) >= 2 and args[0].startswith("@"):
                t = await resolve_target(message, bot, pool, args=args, bot_id=bid)
                if not t:
                    await message.reply("💫 I couldn't find that person.")
                    return
                tag = args[1].lower()[:32]
                res = await c.execute("UPDATE ax_user_friends SET tag_name=$4 WHERE bot_id=$1 AND owner_id=$2 AND friend_id=$3", bid, uid, t.id, tag)
                ok = res.split()[-1] != "0"
                await message.reply(f"🏷 <b>{html.escape(t.first_name or 'Friend')}</b> is now tagged <code>{html.escape(tag)}</code>." if ok else "They are not in your friends list. Use /addf first.", parse_mode=H)
            elif len(args) >= 2:
                old, new = args[0].lower()[:32], args[1].lower()[:32]
                res = await c.execute("UPDATE ax_user_friends SET tag_name=$4 WHERE bot_id=$1 AND owner_id=$2 AND LOWER(tag_name)=$3", bid, uid, old, new)
                n = int(res.split()[-1])
                await message.reply(f"🏷 Renamed <code>{html.escape(old)}</code> to <code>{html.escape(new)}</code> for <b>{n}</b> friend(s)." if n else "No friends with that tag.", parse_mode=H)
            else:
                await message.reply("Use: <code>/ctag old new</code> to rename a tag, or reply to a friend with <code>/ctag newtag</code>.", parse_mode=H)

async def _group_all(message: Message, bot: Bot, pool: asyncpg.Pool, bot_id: int) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    k = (bot_id, message.chat.id)
    now = time.monotonic()
    if now - _all_cd.get(k, -1e9) < 300:
        await message.reply("⏳ Cooldown active for <code>/all</code> &amp; <code>@all</code>.", parse_mode=H)
        return
    _all_cd[k] = now
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT u.id, COALESCE(u.first_name,'Member') AS name FROM ax_chat_members m JOIN users u ON u.id=m.user_id WHERE m.bot_id=$1 AND m.chat_id=$2 LIMIT 100", bot_id, message.chat.id)
    async def _go():
        for i in range(0, len(rows), 5):
            line = " ".join(f'<a href="tg://user?id={r["id"]}">{html.escape(r["name"])}</a>' for r in rows[i:i+5])
            try:
                await bot.send_message(message.chat.id, line, parse_mode=H)
                await asyncio.sleep(1.5)
            except Exception:
                break
    fire(_go())

@router.message(Command("all"))
async def slash_all(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await _group_all(message, bot, pool, bot_ctx.bot_id)

@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.regexp(r"(?i)(?:^|\s)@all(?:\s|$)"))
async def at_all(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    await _group_all(message, bot, pool, bot_ctx.bot_id)

@router.message(Command("setintro"))
async def setintro_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    txt = cmd_arg_text(message)[:400]
    if not txt:
        await message.reply("Use: <code>/setintro Your bio here</code>", parse_mode=H)
        return
    async with pool.acquire() as c:
        await _ensure_tables(c)
        await c.execute("INSERT INTO ax_user_intros (bot_id, user_id, intro_text) VALUES ($1,$2,$3) ON CONFLICT (bot_id, user_id) DO UPDATE SET intro_text=EXCLUDED.intro_text, updated_at=now()", bot_ctx.bot_id, message.from_user.id, txt)
    await message.reply("✅ Intro saved!", parse_mode=H)

@router.message(Command("intro"))
async def intro_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    uid = message.reply_to_message.from_user.id if (message.reply_to_message and message.reply_to_message.from_user) else message.from_user.id
    async with pool.acquire() as c:
        await _ensure_tables(c)
        row = await c.fetchrow("SELECT intro_text FROM ax_user_intros WHERE bot_id=$1 AND user_id=$2", bot_ctx.bot_id, uid)
    await message.reply(f"🪪 <b>INTRO</b>\n<blockquote>{html.escape(row['intro_text']) if row else 'No intro set.'}</blockquote>", parse_mode=H)

@router.message(Command("detail"))
async def detail_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user:
        return
    import ax_premium
    is_prem = (message.from_user.id in OWNER_IDS) or (await ax_premium.get_premium(pool, message.from_user.id, bot_id=bot_ctx.bot_id) is not None)
    args = cmd_args(message)
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
    elif args and is_prem and args[0].lstrip("-").isdigit():
        uid = int(args[0])
    else:
        await message.reply("Reply with <code>/detail</code> (or Premium: <code>/detail &lt;user_id&gt;</code>).", parse_mode=H)
        return
    async with pool.acquire() as c:
        await _ensure_tables(c)
        h = await c.fetchrow("SELECT names_json, usernames_json FROM ax_name_history WHERE bot_id=$1 AND user_id=$2", bot_ctx.bot_id, uid)
    names = json.loads(h["names_json"])[-4:] if h else []
    unames = json.loads(h["usernames_json"])[-4:] if h else []
    await message.reply(f"🔎 <b>DETAIL</b>\n<blockquote>ID: <code>{uid}</code>\nLast 4 Names: {names}\nLast 4 Usernames: {unames}</blockquote>", parse_mode=H)

# ---- name history: records the last 4 names / usernames so /detail has something to show ----
TRACK_NAME_HISTORY = True
_names_seen: dict[tuple[int, int], tuple[str, str]] = {}
_NH_MW = False


async def _save_names(pool, bot_id: int, user_id: int, name: str, username: str) -> None:
    try:
        async with pool.acquire() as c:
            await _ensure_tables(c)
            row = await c.fetchrow(
                "SELECT names_json, usernames_json FROM ax_name_history WHERE bot_id=$1 AND user_id=$2",
                bot_id, user_id)
            names = json.loads(row["names_json"]) if row else []
            unames = json.loads(row["usernames_json"]) if row else []
            n2 = _push_last4(names, name)
            u2 = _push_last4(unames, username)
            if row and n2 == names and u2 == unames:
                return
            await c.execute(
                "INSERT INTO ax_name_history (bot_id, user_id, names_json, usernames_json) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (bot_id, user_id) DO UPDATE SET names_json=EXCLUDED.names_json, "
                "usernames_json=EXCLUDED.usernames_json, updated_at=now()",
                bot_id, user_id, json.dumps(n2, ensure_ascii=False), json.dumps(u2, ensure_ascii=False))
    except Exception:
        log.exception("name history save failed")


class NameHistoryMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            u = getattr(event, "from_user", None)
            pool = data.get("pool")
            if TRACK_NAME_HISTORY and u is not None and not u.is_bot and pool is not None:
                ctx = data.get("bot_ctx")
                bid = ctx.bot_id if ctx is not None else (data.get("bot_id") or 0)
                cur = ((u.full_name or "").strip(), (u.username or "").strip())
                key = (bid, u.id)
                if _names_seen.get(key) != cur:
                    if len(_names_seen) > 50000:
                        _names_seen.clear()
                    _names_seen[key] = cur
                    fire(_save_names(pool, bid, u.id, cur[0], cur[1]))
        except Exception:
            log.exception("name history watcher failed")
        return await handler(event, data)


def register(dp):
    global _NH_MW
    if not _NH_MW:
        _NH_MW = True
        dp.message.outer_middleware(NameHistoryMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

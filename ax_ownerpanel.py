"""Separate Owner-only control panel: /ownerpanel and /owneruser (aiogram 3.x).
Completely separate from ax_adminpanel.py (/adminpanel is untouched).
Non-owners get no reply at all. Every owner action is saved in ax_owner_audit."""
import html
import logging
import os
import resource
import sys
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject

import config
from ax_common import btn, cmd_args
from bot_context import BotContext
from services import ensure_user_and_wallet

log = logging.getLogger(__name__)
router = Router(name="ax_ownerpanel")

# Strictly your 3 Owner IDs only
OWNER_IDS = {8151412244, 8920362130, 6584534013}

PAGE = 10
SYMBOL = getattr(config, "CURRENCY_SYMBOL", "$")
MEDALS = ["🥇", "🥈", "🥉"]
H = ParseMode.HTML

_BOOT_TIME = time.monotonic()
_maintenance_on = False
_middleware_registered = False
_clone_pool: asyncpg.Pool | None = None
_clone_pool_tried = False


def _fmt(n) -> str:
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_uptime() -> str:
    secs = int(time.monotonic() - _BOOT_TIME)
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def _ram_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024.0 * 1024.0) if sys.platform == "darwin" else rss / 1024.0


async def _ensure_tables(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_owner_settings ("
            "key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_owner_audit ("
            "id BIGSERIAL PRIMARY KEY, "
            "owner_id BIGINT NOT NULL, "
            "action TEXT NOT NULL, "
            "target TEXT, "
            "details TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )


async def log_audit(
    pool: asyncpg.Pool,
    owner_id: int,
    action: str,
    target: str | None = None,
    details: str | None = None,
) -> None:
    try:
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_owner_audit (owner_id, action, target, details) "
                "VALUES ($1, $2, $3, $4)",
                owner_id,
                action,
                target,
                details,
            )
    except Exception as exc:
        log.warning("owner audit log write failed: %s", exc)


async def _get_clone_pool() -> asyncpg.Pool | None:
    """Optional pool for the separate Clone DB (only opens if CLONE_DATABASE_URL is set)."""
    global _clone_pool, _clone_pool_tried
    dsn = (os.environ.get("CLONE_DATABASE_URL") or "").strip()
    if not dsn:
        return None
    if _clone_pool is not None:
        return _clone_pool
    if _clone_pool_tried:
        return None
    _clone_pool_tried = True
    try:
        _clone_pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=3,
            statement_cache_size=0,
            timeout=15,
        )
        return _clone_pool
    except Exception as exc:
        log.warning("Could not connect to CLONE_DATABASE_URL: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Maintenance Mode Middleware (non-owners blocked when ON, owners bypass)
# ---------------------------------------------------------------------------
class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if _maintenance_on:
            user = getattr(event, "from_user", None)
            if user is None or user.id not in OWNER_IDS:
                if isinstance(event, Message):
                    txt = (event.text or event.caption or "").strip()
                    if txt.startswith("/"):
                        try:
                            await event.reply(
                                "🛠 <b>Maintenance Mode</b>\n"
                                "<blockquote>Rumi is being updated right now. Please try again in a few minutes ✨</blockquote>",
                                parse_mode=H,
                            )
                        except Exception:
                            pass
                    return None
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer("🛠 Maintenance mode is active. Try again soon.", show_alert=True)
                    except Exception:
                        pass
                    return None
        return await handler(event, data)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def home_view() -> tuple[str, InlineKeyboardMarkup]:
    m_status = "🔴 ON" if _maintenance_on else "🟢 OFF"
    clone_status = "🟢 Connected" if os.environ.get("CLONE_DATABASE_URL") else "⚪ Not linked yet"
    text = (
        "👑 <b>RUMI OWNER PANEL</b>\n"
        f"<blockquote>🛠 <b>Maintenance:</b> {m_status}\n"
        f"🤖 <b>Clone DB:</b> {clone_status}\n"
        f"⏱ <b>Uptime:</b> <code>{_fmt_uptime()}</code> • 💾 <code>{_ram_mb():.1f} MB</code></blockquote>\n"
        "<i>Choose a section below. Every action is saved in the Audit Log.</i>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn("📊 Stats & Health", "axop:stats", style="success"),
                btn("👤 User Manager", "axop:userhelp", style="primary"),
            ],
            [
                btn("🏆 Groups", "axop:groups:cash:1", style="primary"),
                btn("📢 Channels", "axop:channels:1", style="primary"),
            ],
            [
                btn("💎 Premium List", "axop:prem:1"),
                btn("🚫 Banned List", "axop:bans:1"),
            ],
            [
                btn("⭐ Donors", "axop:donors:1"),
                btn("🤖 Clones Platform", "axop:clones:1", style="primary"),
            ],
            [btn("🧠 AI Status", "axop:ai", style="primary")],
            [
                btn("📜 Audit Log", "axop:audit:1"),
                btn("🪙 Reset Coins", "axop:resetask", style="danger"),
                btn(
                    "🛠 Maint: ON" if _maintenance_on else "🛠 Maint: OFF",
                    "axop:maint",
                    style="danger" if _maintenance_on else "success",
                ),
            ],
        ]
    )
    return text, kb


async def groups_view(pool: asyncpg.Pool, sort: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    order = "members" if sort == "members" else "total"
    async with pool.acquire() as c:
        total_groups = await c.fetchval("SELECT COUNT(*) FROM ax_groups")
        pages = max(1, -(-total_groups // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT g.chat_id, g.title, COALESCE(SUM(w.cash),0)::bigint AS total, "
            "COUNT(m.user_id) AS members FROM ax_groups g "
            "LEFT JOIN ax_chat_members m ON m.chat_id = g.chat_id "
            "LEFT JOIN wallets w ON w.user_id = m.user_id "
            f"GROUP BY g.chat_id, g.title ORDER BY {order} DESC, g.chat_id "
            "LIMIT $1 OFFSET $2",
            PAGE,
            (page - 1) * PAGE,
        )

    lines = []
    for i, r in enumerate(rows):
        rank = (page - 1) * PAGE + i + 1
        medal = MEDALS[rank - 1] if rank <= 3 else f"<code>#{rank:02d}</code>"
        name = html.escape(r["title"] or "Group")
        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"      <code>{SYMBOL}{_fmt(r['total'])}</code> • {r['members']} members\n"
            f"      <code>{r['chat_id']}</code>"
        )

    body = "\n".join(lines) if lines else "No groups tracked yet."
    label = "Members" if sort == "members" else "Richest"
    text = (
        "🏆 <b>OWNER PANEL • GROUPS</b>\n"
        f"<i>Sorted by {label} • {total_groups} total • page {page}/{pages}</i>\n\n"
        f"<blockquote>{body}</blockquote>"
    )
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axop:groups:{sort}:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axop:groups:{sort}:{page + 1}"))

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn("💵 Richest", "axop:groups:cash:1", style="primary" if sort != "members" else None),
                btn("👥 Members", "axop:groups:members:1", style="primary" if sort == "members" else None),
            ],
            nav,
            [btn("🏠 Owner Home", "axop:home")],
        ]
    )
    return text, kb


async def channels_view(pool: asyncpg.Pool, page: int) -> tuple[str, InlineKeyboardMarkup]:
    async with pool.acquire() as c:
        total = await c.fetchval("SELECT COUNT(*) FROM ax_channels")
        pages = max(1, -(-total // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT chat_id, title, added_by, last_count FROM ax_channels "
            "ORDER BY last_count DESC NULLS LAST, chat_id LIMIT $1 OFFSET $2",
            PAGE,
            (page - 1) * PAGE,
        )

    lines = []
    for i, r in enumerate(rows):
        rank = (page - 1) * PAGE + i + 1
        medal = MEDALS[rank - 1] if rank <= 3 else f"<code>#{rank:02d}</code>"
        name = html.escape(r["title"] or "Channel")
        cnt = "?" if r["last_count"] is None else _fmt(r["last_count"])
        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"      <code>{cnt}</code> members • by <code>{r['added_by']}</code>\n"
            f"      <code>{r['chat_id']}</code>"
        )

    body = "\n".join(lines) if lines else "No channels tracked yet."
    text = (
        "📢 <b>OWNER PANEL • CHANNELS</b>\n"
        f"<i>{total} total • page {page}/{pages}</i>\n\n"
        f"<blockquote>{body}</blockquote>"
    )
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axop:channels:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axop:channels:{page + 1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav, [btn("🏠 Owner Home", "axop:home")]])
    return text, kb


async def donors_view(pool: asyncpg.Pool, page: int) -> tuple[str, InlineKeyboardMarkup]:
    async with pool.acquire() as c:
        try:
            agg = await c.fetchrow("SELECT COUNT(*) AS n, COALESCE(SUM(stars),0) AS s FROM ax_donations")
            total = agg["n"]
            stars = agg["s"]
            pages = max(1, -(-total // PAGE))
            page = min(max(1, page), pages)
            rows = await c.fetch(
                "SELECT user_id, first_name, stars, created_at FROM ax_donations "
                "ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                PAGE,
                (page - 1) * PAGE,
            )
        except Exception:
            total, stars, pages, rows = 0, 0, 1, []

    lines = [
        f"⭐ <b>{html.escape(r['first_name'] or 'User')}</b> — <code>{r['stars']} ⭐</code> "
        f"(<code>{r['user_id']}</code>)"
        for r in rows
    ]
    body = "\n".join(lines) if lines else "No donations yet."
    text = (
        "⭐ <b>OWNER PANEL • DONORS</b>\n"
        f"<i>{total} gifts • {stars:,} Stars total • page {page}/{pages}</i>\n\n"
        f"<blockquote>{body}</blockquote>"
    )
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axop:donors:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axop:donors:{page + 1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav, [btn("🏠 Owner Home", "axop:home")]])
    return text, kb


async def stats_view(pool: asyncpg.Pool) -> tuple[str, InlineKeyboardMarkup]:
    async with pool.acquire() as c:
        users = await c.fetchval("SELECT COUNT(*) FROM users")
        groups = await c.fetchval("SELECT COUNT(*) FROM ax_groups")
        channels = await c.fetchval("SELECT COUNT(*) FROM ax_channels")
        total_cash = await c.fetchval("SELECT COALESCE(SUM(cash),0)::bigint FROM wallets")
        total_gems = await c.fetchval("SELECT COALESCE(SUM(gems),0)::bigint FROM wallets")
        bans = await c.fetchval("SELECT COUNT(*) FROM ax_bot_bans")
        db_size = await c.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
        try:
            prem_count = await c.fetchval("SELECT COUNT(*) FROM ax_premium")
        except Exception:
            prem_count = 0
        try:
            stars_sum = await c.fetchval("SELECT COALESCE(SUM(stars),0) FROM ax_donations")
        except Exception:
            stars_sum = 0

    text = (
        "📊 <b>BOT STATS & HEALTH</b>\n\n"
        "<blockquote>"
        f"👥 <b>Users:</b> <code>{users:,}</code>\n"
        f"🏰 <b>Groups:</b> <code>{groups:,}</code>\n"
        f"📢 <b>Channels:</b> <code>{channels:,}</code>\n"
        f"💰 <b>Total Cash:</b> <code>{SYMBOL}{_fmt(total_cash)}</code>\n"
        f"💎 <b>Total Gems:</b> <code>{total_gems:,}</code>\n"
        f"🌟 <b>Premium Users:</b> <code>{prem_count:,}</code>\n"
        f"⭐ <b>Donation Stars:</b> <code>{stars_sum:,}</code>\n"
        f"🚫 <b>Bot-Banned:</b> <code>{bans:,}</code>"
        "</blockquote>\n\n"
        "<blockquote>"
        f"🗄 <b>DB Size:</b> <code>{html.escape(str(db_size))}</code>\n"
        f"💾 <b>Peak RAM:</b> <code>{_ram_mb():.1f} MB</code>\n"
        f"⏱ <b>Uptime:</b> <code>{_fmt_uptime()}</code>\n"
        f"🛠 <b>Maintenance:</b> {'🔴 ON' if _maintenance_on else '🟢 OFF'}"
        "</blockquote>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🔄 Refresh", "axop:stats", style="primary")],
            [btn("🏠 Owner Home", "axop:home")],
        ]
    )
    return text, kb


async def user_view(pool: asyncpg.Pool, uid: int) -> tuple[str, InlineKeyboardMarkup] | tuple[None, None]:
    import ax_premium

    await ax_premium.ensure(pool)
    async with pool.acquire() as c:
        u = await c.fetchrow("SELECT id, username, first_name FROM users WHERE id=$1", uid)
        w = await c.fetchrow("SELECT cash, gems, level, xp, vault FROM wallets WHERE user_id=$1", uid)
        banned = bool(await c.fetchval("SELECT 1 FROM ax_bot_bans WHERE user_id=$1", uid))
        prem = bool(await c.fetchval("SELECT 1 FROM ax_premium WHERE user_id=$1", uid))
        grows = await c.fetch(
            "SELECT item_id, qty FROM ax_gift_recv WHERE user_id=$1 AND qty>0 ORDER BY item_id", uid
        )

    if u is None and w is None:
        return None, None

    name = html.escape((u["first_name"] if u else None) or "Player")
    uname = f"@{html.escape(u['username'])}" if (u and u["username"]) else "—"
    cash = w["cash"] if w else 0
    vault = w["vault"] if w else 0
    gems = w["gems"] if w else 0
    lvl = w["level"] if w else 1
    import ax_gift

    gifts_txt = ", ".join(
        f"{ax_gift.ITEMS[r['item_id']][0]}×{r['qty']}" for r in grows if r["item_id"] in ax_gift.ITEMS
    ) or "none"

    text = (
        f"👤 <b>USER MANAGER</b>\n"
        f"<blockquote>"
        f"📛 <b>Name:</b> {name} ({uname})\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"💰 <b>Cash:</b> <code>{SYMBOL}{cash:,}</code> (Vault: <code>{SYMBOL}{vault:,}</code>)\n"
        f"💎 <b>Gems:</b> <code>{gems:,}</code> • ⭐ <b>LVL:</b> <code>{lvl}</code>\n"
        f"🌟 <b>Premium:</b> {'✅ Yes' if prem else '❌ No'}\n"
        f"🚫 <b>Bot-Banned:</b> {'🔴 Yes' if banned else '🟢 No'}\n"
        f"🎁 <b>Gifts:</b> {gifts_txt}"
        f"</blockquote>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn("➕ +10K", f"axop:uc:{uid}:10000", style="success"),
                btn("➕ +100K", f"axop:uc:{uid}:100000", style="success"),
                btn("➖ -10K", f"axop:uc:{uid}:-10000", style="danger"),
            ],
            [
                btn(
                    "❌ Remove Premium" if prem else "💎 Give Premium",
                    f"axop:up:{uid}:{'0' if prem else '1'}",
                    style="danger" if prem else "primary",
                ),
                btn(
                    "✅ Unban" if banned else "🚫 Ban Bot-wide",
                    f"axop:ub:{uid}:{'0' if banned else '1'}",
                    style="success" if banned else "danger",
                ),
            ],
            [
                btn("🎁 Give Gift", f"axop:gl:{uid}:g", style="success"),
                btn("🗑 Remove Gift", f"axop:gl:{uid}:r", style="danger"),
            ],
            [btn("🔄 Refresh", f"axop:uv:{uid}"), btn("🏠 Owner Home", "axop:home")],
        ]
    )
    return text, kb


async def bans_view(pool: asyncpg.Pool, page: int) -> tuple[str, InlineKeyboardMarkup]:
    async with pool.acquire() as c:
        total = await c.fetchval("SELECT COUNT(*) FROM ax_bot_bans")
        pages = max(1, -(-total // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT b.user_id, COALESCE(u.first_name, u.username, 'User') AS name "
            "FROM ax_bot_bans b LEFT JOIN users u ON u.id = b.user_id "
            "ORDER BY b.user_id LIMIT $1 OFFSET $2",
            PAGE,
            (page - 1) * PAGE,
        )
    lines = [f"• <b>{html.escape(r['name'])}</b> — <code>{r['user_id']}</code>" for r in rows]
    body = "\n".join(lines) if lines else "No bot-banned users."
    text = (
        f"🚫 <b>BOT-BANNED USERS</b>\n<i>{total} total • page {page}/{pages}</i>\n\n"
        f"<blockquote>{body}</blockquote>\n"
        "<i>Tap a user below to manage or unban.</i>"
    )
    u_btns = [btn(f"👤 {r['user_id']}", f"axop:uv:{r['user_id']}") for r in rows]
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axop:bans:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axop:bans:{page + 1}"))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            *[u_btns[i : i + 2] for i in range(0, len(u_btns), 2)],
            nav,
            [btn("🏠 Owner Home", "axop:home")],
        ]
    )
    return text, kb


async def prem_view(pool: asyncpg.Pool, page: int) -> tuple[str, InlineKeyboardMarkup]:
    import ax_premium

    await ax_premium.ensure(pool)
    async with pool.acquire() as c:
        total = await c.fetchval("SELECT COUNT(*) FROM ax_premium")
        pages = max(1, -(-total // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT p.user_id, p.since, COALESCE(u.first_name, u.username, 'User') AS name "
            "FROM ax_premium p LEFT JOIN users u ON u.id = p.user_id "
            "ORDER BY p.since DESC LIMIT $1 OFFSET $2",
            PAGE,
            (page - 1) * PAGE,
        )
    lines = [
        f"💎 <b>{html.escape(r['name'])}</b> — <code>{r['user_id']}</code> ({r['since']:%d %b})"
        for r in rows
    ]
    body = "\n".join(lines) if lines else "No Premium users yet."
    text = (
        f"💎 <b>PREMIUM USERS</b>\n<i>{total} total • page {page}/{pages}</i>\n\n"
        f"<blockquote>{body}</blockquote>"
    )
    u_btns = [btn(f"👤 {r['user_id']}", f"axop:uv:{r['user_id']}") for r in rows]
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axop:prem:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axop:prem:{page + 1}"))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            *[u_btns[i : i + 2] for i in range(0, len(u_btns), 2)],
            nav,
            [btn("🏠 Owner Home", "axop:home")],
        ]
    )
    return text, kb


async def audit_view(pool: asyncpg.Pool, page: int) -> tuple[str, InlineKeyboardMarkup]:
    async with pool.acquire() as c:
        total = await c.fetchval("SELECT COUNT(*) FROM ax_owner_audit")
        pages = max(1, -(-total // PAGE))
        page = min(max(1, page), pages)
        rows = await c.fetch(
            "SELECT owner_id, action, target, details, created_at FROM ax_owner_audit "
            "ORDER BY id DESC LIMIT $1 OFFSET $2",
            PAGE,
            (page - 1) * PAGE,
        )
    lines = []
    for r in rows:
        t = r["created_at"].strftime("%d %b %H:%M")
        tgt = f" → <code>{html.escape(r['target'])}</code>" if r["target"] else ""
        det = f" ({html.escape(r['details'][:40])})" if r["details"] else ""
        lines.append(f"<code>{t}</code> <b>{html.escape(r['action'])}</b>{tgt}{det}")
    body = "\n".join(lines) if lines else "No owner actions recorded yet."
    text = (
        f"📜 <b>OWNER AUDIT LOG</b>\n<i>{total} entries • page {page}/{pages}</i>\n\n"
        f"<blockquote>{body}</blockquote>"
    )
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axop:audit:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axop:audit:{page + 1}"))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[nav, [btn("🔄 Refresh", "axop:audit:1"), btn("🏠 Owner Home", "axop:home")]]
    )
    return text, kb


async def clones_view(page: int) -> tuple[str, InlineKeyboardMarkup]:
    cpool = await _get_clone_pool()
    if cpool is None:
        text = (
            "🤖 <b>CLONES PLATFORM</b>\n\n"
            "<blockquote>⚪ <b>Clone DB is not connected yet.</b>\n\n"
            "Once we deploy the Clone Engine and add <code>CLONE_DATABASE_URL</code> "
            "to main Rumi's environment variables, all hosted clones, plans (Normal/Pro), "
            "pause/resume controls, and Stars stats will appear here automatically.</blockquote>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🏠 Owner Home", "axop:home")]])
        return text, kb

    try:
        async with cpool.acquire() as c:
            total = await c.fetchval("SELECT COUNT(*) FROM clone_bots")
            pages = max(1, -(-total // PAGE))
            page = min(max(1, page), pages)
            rows = await c.fetch(
                "SELECT bot_id, username, owner_id, bot_type, plan, status "
                "FROM clone_bots ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                PAGE,
                (page - 1) * PAGE,
            )
    except Exception as exc:
        text = (
            "🤖 <b>CLONES PLATFORM</b>\n\n"
            f"<blockquote>⚠️ Connected to Clone DB, waiting for <code>clone_bots</code> table.\n"
            f"<i>{html.escape(str(exc)[:120])}</i></blockquote>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🏠 Owner Home", "axop:home")]])
        return text, kb

    lines = []
    for r in rows:
        st_ico = "🟢" if r["status"] == "active" else "⏸" if r["status"] == "paused" else "🔴"
        uname = html.escape(r["username"] or str(r["bot_id"]))
        lines.append(
            f"{st_ico} <b>@{uname}</b> • <code>{html.escape(r['bot_type'])}</code> • "
            f"<b>{html.escape(r['plan'])}</b>\n      Owner: <code>{r['owner_id']}</code>"
        )
    body = "\n".join(lines) if lines else "No clones registered yet."
    text = (
        f"🤖 <b>CLONES PLATFORM</b>\n<i>{total} clones • page {page}/{pages}</i>\n\n"
        f"<blockquote>{body}</blockquote>"
    )
    nav = []
    if page > 1:
        nav.append(btn("◀", f"axop:clones:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "noop"))
    if page < pages:
        nav.append(btn("▶", f"axop:clones:{page + 1}"))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[nav, [btn("🔄 Refresh", f"axop:clones:{page}"), btn("🏠 Owner Home", "axop:home")]]
    )
    return text, kb


# ---------------------------------------------------------------------------
# Commands & Callbacks
# ---------------------------------------------------------------------------
@router.message(Command("ownerpanel"))
async def ownerpanel_cmd(message: Message) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return  # silent for everyone else
    text, kb = home_view()
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.message(Command("owneruser"))
async def owneruser_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return
    uid = None
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
    else:
        args = cmd_args(message)
        if not args:
            await message.reply(
                "👤 <b>User Manager</b>\n<blockquote>Use <code>/owneruser USER_ID</code>, "
                "<code>/owneruser @username</code>, or reply to a user.</blockquote>",
                parse_mode=H,
            )
            return
        raw = args[0].lstrip("@")
        if raw.isdigit():
            uid = int(raw)
        else:
            async with pool.acquire() as c:
                uid = await c.fetchval(
                    "SELECT id FROM users WHERE LOWER(username) = LOWER($1) LIMIT 1",
                    raw,
                )
    if uid is None:
        await message.reply("🔎 User not found in database.")
        return
    text, kb = await user_view(pool, uid)
    if text is None:
        await message.reply("🔎 User has no record yet.")
        return
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.callback_query(F.data.startswith("axop:"))
async def owner_panel_callback(query: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    global _maintenance_on
    owner_id = query.from_user.id
    if owner_id not in OWNER_IDS:
        await query.answer()
        return

    parts = (query.data or "").split(":")
    sec = parts[1] if len(parts) > 1 else "home"
    alert_text = None

    if sec == "home":
        text, kb = home_view()
    elif sec == "stats":
        text, kb = await stats_view(pool)
    elif sec == "ai":
        import ai_engine

        await query.answer("Testing AI models...")
        try:
            lines = await ai_engine.status()
        except Exception as exc:
            lines = ["status failed: %s" % exc]
        text = "🧠 <b>AI STATUS</b>\n<pre>" + html.escape("\n".join(lines))[:3600] + "</pre>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [btn("🔄 Refresh", "axop:ai", style="primary")],
                [btn("🏠 Owner Home", "axop:home")],
            ]
        )
        if query.message:
            try:
                await query.message.edit_text(text, parse_mode=H, reply_markup=kb)
            except Exception:
                pass
        return
    elif sec == "groups":
        sort = parts[2] if len(parts) > 2 else "cash"
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        text, kb = await groups_view(pool, sort, page)
    elif sec == "channels":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        text, kb = await channels_view(pool, page)
    elif sec == "donors":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        text, kb = await donors_view(pool, page)
    elif sec == "userhelp":
        text = (
            "👤 <b>USER MANAGER</b>\n\n"
            "<blockquote>Search any user with:\n"
            "• <code>/owneruser 123456789</code>\n"
            "• <code>/owneruser @username</code>\n"
            "• Or reply to their message with <code>/owneruser</code>\n\n"
            "From there you can add/remove coins, toggle Premium, give/remove gifts, or ban/unban bot-wide.</blockquote>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🏠 Owner Home", "axop:home")]])
    elif sec == "bans":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        text, kb = await bans_view(pool, page)
    elif sec == "prem":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        text, kb = await prem_view(pool, page)
    elif sec == "audit":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        text, kb = await audit_view(pool, page)
    elif sec == "resetask":
        async with pool.acquire() as c:
            stats = await c.fetchrow(
                "SELECT COUNT(*) AS n, COALESCE(SUM(cash),0)::bigint AS tot_cash FROM wallets"
            )
        text = (
            "🪙 <b>MANUAL COIN RESET (SAFE 2-STEP)</b>\n\n"
            "<blockquote>"
            f"👥 <b>Wallets affected:</b> <code>{int(stats['n']):,}</code>\n"
            f"💰 <b>Current total cash:</b> <code>{SYMBOL}{_fmt(stats['tot_cash'])}</code>\n"
            "✨ <b>EXP &amp; Level:</b> <code>UNTOUCHED</code>\n"
            "💎 <b>Gems:</b> <code>UNTOUCHED</code>\n"
            "🏆 <b>Season Legends:</b> Top 25 saved automatically before reset"
            "</blockquote>\n\n"
            "<i>Tap <b>Confirm Reset Coins</b> only when you are sure. Accidental taps can be cancelled safely.</i>"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    btn("💥 Confirm Reset Coins", "axop:resetgo", style="danger"),
                    btn("🛡 Cancel (Safe)", "axop:home", style="success"),
                ]
            ]
        )
    elif sec == "resetgo":
        season_tag = f"Season-{datetime.now(timezone.utc):%Y-%m-%d}"
        async with pool.acquire() as c:
            async with c.transaction():
                await c.execute(
                    "CREATE TABLE IF NOT EXISTS ax_season_legends ("
                    "id BIGSERIAL PRIMARY KEY, "
                    "bot_id BIGINT NOT NULL DEFAULT 0, "
                    "season_tag TEXT NOT NULL, "
                    "user_id BIGINT NOT NULL, "
                    "display_name TEXT NOT NULL, "
                    "cash_snapshot BIGINT NOT NULL DEFAULT 0, "
                    "gems_snapshot BIGINT NOT NULL DEFAULT 0, "
                    "xp_snapshot BIGINT NOT NULL DEFAULT 0, "
                    "level_snapshot INT NOT NULL DEFAULT 1, "
                    "recorded_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
                top_rows = await c.fetch(
                    "SELECT w.bot_id, w.user_id, COALESCE(u.first_name, u.username, 'Player') AS name, "
                    "w.cash, w.gems, w.xp, w.level "
                    "FROM wallets w LEFT JOIN users u ON u.id = w.user_id AND u.bot_id = w.bot_id "
                    "ORDER BY w.cash DESC, w.xp DESC LIMIT 25"
                )
                for tr in top_rows:
                    await c.execute(
                        "INSERT INTO ax_season_legends "
                        "(bot_id, season_tag, user_id, display_name, cash_snapshot, gems_snapshot, xp_snapshot, level_snapshot) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                        tr["bot_id"] or 0,
                        season_tag,
                        tr["user_id"],
                        tr["name"],
                        int(tr["cash"]),
                        int(tr["gems"]),
                        int(tr["xp"]),
                        int(tr["level"]),
                    )
                reset_status = await c.execute(
                    "UPDATE wallets SET cash = 0, vault = 0, daily_transferred = 0, updated_at = now()"
                )
        reset_cnt = int(reset_status.split()[-1]) if reset_status.split() and reset_status.split()[-1].isdigit() else 0
        await log_audit(
            pool,
            owner_id,
            "season_coin_reset",
            target=season_tag,
            details=f"reset={reset_cnt} wallets, legends={len(top_rows)}",
        )
        alert_text = f"🪙 Reset coins for {reset_cnt} wallets"
        text = (
            "✅ <b>SEASON COINS RESET COMPLETE</b>\n\n"
            "<blockquote>"
            f"🏷 <b>Season Tag:</b> <code>{html.escape(season_tag)}</code>\n"
            f"🪙 <b>Wallets Reset:</b> <code>{reset_cnt:,}</code> (Cash &amp; Vault → 0)\n"
            f"🏆 <b>Legends Saved:</b> <code>{len(top_rows)}</code> (view via <code>/toplegends</code>)\n"
            "✨ <b>EXP &amp; Gems:</b> <code>UNTOUCHED</code>"
            "</blockquote>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🏠 Owner Home", "axop:home")]])
    elif sec == "clones":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        text, kb = await clones_view(page)
    elif sec == "maint":
        _maintenance_on = not _maintenance_on
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO ax_owner_settings (key, value, updated_at) VALUES ('maintenance', $1, now()) "
                "ON CONFLICT (key) DO UPDATE SET value=$1, updated_at=now()",
                "1" if _maintenance_on else "0",
            )
        await log_audit(pool, owner_id, "maintenance", details="ON" if _maintenance_on else "OFF")
        alert_text = "🛠 Maintenance mode ON" if _maintenance_on else "✅ Maintenance mode OFF"
        text, kb = home_view()
    elif sec == "uv" and len(parts) >= 3 and parts[2].isdigit():
        uid = int(parts[2])
        text, kb = await user_view(pool, uid)
        if text is None:
            await query.answer("User not found", show_alert=True)
            return
    elif sec == "uc" and len(parts) >= 4 and parts[2].isdigit():
        uid = int(parts[2])
        delta = int(parts[3])
        await ensure_user_and_wallet(pool, uid, None, "Player")
        async with pool.acquire() as c:
            await c.execute(
                "UPDATE wallets SET cash = GREATEST(0, cash + $1), updated_at = now() WHERE user_id = $2",
                delta,
                uid,
            )
        await log_audit(pool, owner_id, "coins_adjust", target=str(uid), details=f"{delta:+d}")
        alert_text = f"Updated coins ({delta:+,})"
        text, kb = await user_view(pool, uid)
    elif sec == "up" and len(parts) >= 4 and parts[2].isdigit():
        import ax_premium

        uid = int(parts[2])
        grant = parts[3] == "1"
        await ax_premium.ensure(pool)
        async with pool.acquire() as c:
            if grant:
                await c.execute(
                    "INSERT INTO ax_premium (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                    uid,
                )
            else:
                await c.execute("DELETE FROM ax_premium WHERE user_id=$1", uid)
        ax_premium.invalidate(uid)
        await log_audit(
            pool, owner_id, "premium_grant" if grant else "premium_remove", target=str(uid)
        )
        alert_text = "💎 Premium granted" if grant else "❌ Premium removed"
        text, kb = await user_view(pool, uid)
    elif sec == "gl" and len(parts) >= 4 and parts[2].isdigit() and parts[3] in ("g", "r"):
        import ax_gift

        uid = int(parts[2])
        mode = parts[3]
        items = list(ax_gift.ITEMS.items())
        rows = []
        for i in range(0, len(items), 2):
            rows.append(
                [btn(f"{e} {n}", f"axop:gp:{uid}:{mode}:{iid}") for iid, (e, n, *_r) in items[i : i + 2]]
            )
        rows.append([btn("⬅ Back", f"axop:uv:{uid}")])
        title = "🎁 <b>GIVE GIFT</b>" if mode == "g" else "🗑 <b>REMOVE GIFT</b>"
        text = f"{title}\n<blockquote>User: <code>{uid}</code>\nPick a gift:</blockquote>"
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
    elif sec == "gp" and len(parts) >= 5 and parts[2].isdigit() and parts[3] in ("g", "r"):
        import ax_gift

        uid = int(parts[2])
        mode = parts[3]
        iid = parts[4]
        if iid not in ax_gift.ITEMS:
            await query.answer()
            return
        e, n = ax_gift.ITEMS[iid][0], ax_gift.ITEMS[iid][1]
        qrow = [btn(str(q), f"axop:gq:{uid}:{mode}:{iid}:{q}") for q in range(1, 7)]
        kb = InlineKeyboardMarkup(
            inline_keyboard=[qrow[:3], qrow[3:], [btn("⬅ Back", f"axop:gl:{uid}:{mode}")]]
        )
        verb = "Give" if mode == "g" else "Remove"
        text = f"{e} <b>{verb} {n}</b>\n<blockquote>User: <code>{uid}</code>\nHow many?</blockquote>"
    elif (
        sec == "gq"
        and len(parts) >= 6
        and parts[2].isdigit()
        and parts[3] in ("g", "r")
        and parts[5].isdigit()
    ):
        import ax_gift

        uid = int(parts[2])
        mode = parts[3]
        iid = parts[4]
        qty = int(parts[5])
        if iid not in ax_gift.ITEMS or not 1 <= qty <= 6:
            await query.answer()
            return
        e, n = ax_gift.ITEMS[iid][0], ax_gift.ITEMS[iid][1]
        bid = bot_ctx.bot_id
        async with pool.acquire() as c:
            if mode == "g":
                await c.execute(
                    "INSERT INTO ax_gift_recv (bot_id,user_id,item_id,qty) VALUES ($1,$2,$3,$4) "
                    "ON CONFLICT (user_id,item_id) DO UPDATE SET bot_id=$1, qty=ax_gift_recv.qty+$4",
                    bid,
                    uid,
                    iid,
                    qty,
                )
            else:
                await c.execute(
                    "UPDATE ax_gift_recv SET qty=GREATEST(qty-$1,0) WHERE user_id=$2 AND item_id=$3",
                    qty,
                    uid,
                    iid,
                )
        await log_audit(
            pool,
            owner_id,
            "gift_give" if mode == "g" else "gift_remove",
            target=str(uid),
            details=f"{iid} x{qty}",
        )
        if mode == "g":
            try:
                await query.bot.send_message(
                    uid,
                    f"🎁 You received {e} <b>{n}</b> × <b>{qty}</b>!\nSee your gifts: /items",
                    parse_mode=H,
                )
            except Exception:
                pass
        alert_text = f"{'Gave' if mode == 'g' else 'Removed'} {e} {n} ×{qty}"
        text, kb = await user_view(pool, uid)
    elif sec == "ub" and len(parts) >= 4 and parts[2].isdigit():
        import ax_ban

        uid = int(parts[2])
        do_ban = parts[3] == "1"
        async with pool.acquire() as c:
            if do_ban:
                await c.execute(
                    "INSERT INTO ax_bot_bans (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                    uid,
                )
                ax_ban._banned.add(uid)
            else:
                await c.execute("DELETE FROM ax_bot_bans WHERE user_id=$1", uid)
                ax_ban._banned.discard(uid)
        await log_audit(pool, owner_id, "bot_ban" if do_ban else "bot_unban", target=str(uid))
        alert_text = "🚫 User bot-banned" if do_ban else "✅ User unbanned"
        text, kb = await user_view(pool, uid)
    else:
        await query.answer()
        return

    if query.message and text is not None:
        try:
            await query.message.edit_text(text, parse_mode=H, reply_markup=kb)
        except Exception:
            pass
    await query.answer(alert_text, show_alert=bool(alert_text))


async def on_startup(bot: Bot, dispatcher) -> None:
    global _maintenance_on
    pool: asyncpg.Pool = dispatcher["pool"]
    await _ensure_tables(pool)
    try:
        async with pool.acquire() as c:
            val = await c.fetchval(
                "SELECT value FROM ax_owner_settings WHERE key='maintenance'"
            )
        _maintenance_on = val == "1"
    except Exception:
        _maintenance_on = False


async def on_shutdown(bot: Bot, dispatcher) -> None:
    global _clone_pool, _clone_pool_tried
    if _clone_pool is not None:
        await _clone_pool.close()
        _clone_pool = None
    _clone_pool_tried = False


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        mw = MaintenanceMiddleware()
        dp.message.outer_middleware(mw)
        dp.callback_query.outer_middleware(mw)
    if router.parent_router is None:
        dp.include_router(router)

"""Lock commands: /lock /unlock /locks /locktypes /lockwarns /allowlist /rmallowlist /rmallowlistall (aiogram 3.x)."""
import re

import asyncpg
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

import ax_actions
import ax_lockcore as core
import ax_mod
from ax_common import cmd_args, esc, is_admin

router = Router(name="ax_locks")
_ACT = re.compile(r"\{([^}]+)\}")


async def _gate(message: Message, bot: Bot) -> bool:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False
    if chat.type == "private":
        await ax_mod.say(
            message,
            "⚠️ <b>Groups only</b>\n<blockquote>Use this inside your group 💫</blockquote>",
            bot=bot,
        )
        return False
    if not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await ax_mod.say(
            message,
            "🛡️ <b>Admins only</b>\n<blockquote>You need admin rights for this ✨</blockquote>",
            bot=bot,
        )
        return False
    return True


def _split(message: Message) -> tuple[list[str], str | None, str | None, bool]:
    """Return (types list, reason or None, action or None, has_hash)."""
    text = message.text or ""
    parts = text.split(None, 1)
    body = parts[1] if len(parts) > 1 else ""
    head, sep, tail = body.partition("###")
    types = [t.lower() for t in head.split()]
    action, reason = None, None
    if sep:
        m = _ACT.search(tail)
        if m:
            action = m.group(1).strip()
            tail = _ACT.sub("", tail)
        reason = tail.strip() or None
    return types, reason, action, bool(sep)


@router.message(Command("lock"))
async def lock_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    types, reason, action, has_hash = _split(message)
    if not types:
        await ax_mod.say(
            message,
            "🔒 <b>Lock what?</b>\n<blockquote>Example: /lock sticker photo\n"
            "With action: /lock invitelink ### no ads {ban}\nSee all: /locktypes</blockquote>",
            bot=bot,
        )
        return
    if "all" in types and len(types) > 1:
        types = ["all"]
    bad = [t for t in types if t not in core.LOCK_TYPES]
    if bad:
        await ax_mod.say(
            message,
            f"💫 <b>Unknown lock</b>\n<blockquote>{esc(', '.join(bad))}\nSee /locktypes</blockquote>",
            bot=bot,
        )
        return
    if action and ax_actions.parse_action(action)[0] is None:
        await ax_mod.say(
            message,
            "💫 <b>Unknown action</b>\n<blockquote>Use ban, mute, kick, warn, tmute 6h or tban 1d</blockquote>",
            bot=bot,
        )
        return

    chat_id = message.chat.id
    async with pool.acquire() as c:
        for t in types:
            if has_hash and not action and not reason:
                await c.execute(
                    "UPDATE ax_locks SET action=NULL, reason=NULL WHERE chat_id=$1 AND lock_type=$2",
                    chat_id,
                    t,
                )
                continue
            await c.execute(
                "INSERT INTO ax_locks (chat_id, lock_type, action, reason) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (chat_id, lock_type) DO UPDATE SET action=$3, reason=$4",
                chat_id,
                t,
                action,
                reason,
            )
    core.invalidate(chat_id)
    extra = f"\n⚔️ Action: <b>{esc(action)}</b>" if action else ""
    await ax_mod.say(
        message,
        f"🔒 <b>Locked</b>\n<blockquote>{esc(', '.join(types))}{extra}\n"
        "Only admins and approved users can send these ✨</blockquote>",
        bot=bot,
    )


@router.message(Command("unlock"))
async def unlock_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    types = [t.lower() for t in cmd_args(message)]
    if not types:
        await ax_mod.say(
            message,
            "🔓 <b>Unlock what?</b>\n<blockquote>Example: /unlock sticker photo</blockquote>",
            bot=bot,
        )
        return
    chat_id = message.chat.id
    async with pool.acquire() as c:
        if "all" in types:
            await c.execute("DELETE FROM ax_locks WHERE chat_id=$1", chat_id)
            shown = "everything"
        else:
            await c.execute(
                "DELETE FROM ax_locks WHERE chat_id=$1 AND lock_type = ANY($2::text[])",
                chat_id,
                types,
            )
            shown = ", ".join(types)
    core.invalidate(chat_id)
    await ax_mod.say(
        message,
        f"🔓 <b>Unlocked</b>\n<blockquote>{esc(shown)} ✨</blockquote>",
        bot=bot,
    )


@router.message(Command("locks"))
async def locks_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT lock_type, action FROM ax_locks WHERE chat_id=$1 ORDER BY lock_type",
            chat_id,
        )
    if not rows:
        await ax_mod.say(
            message,
            "🔓 <b>No locks</b>\n<blockquote>Nothing is locked here</blockquote>",
            bot=bot,
        )
        return
    lines = "\n".join(
        f"🔒 {r['lock_type']}" + (f" → {esc(r['action'])}" if r["action"] else "")
        for r in rows
    )
    await ax_mod.say(
        message,
        f"🔒 <b>Locked in this group</b>\n<blockquote>{lines}</blockquote>",
        bot=bot,
    )


@router.message(Command("locktypes"))
async def locktypes_cmd(message: Message, bot: Bot) -> None:
    lines = "\n".join(f"• <code>{k}</code>: {v}" for k, v in core.LOCK_TYPES.items())
    await ax_mod.say(
        message,
        f"📋 <b>Lock types</b>\n<blockquote>{lines}</blockquote>",
        bot=bot,
    )


@router.message(Command("lockwarns"))
async def lockwarns_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = cmd_args(message)
    arg = args[0].lower() if args else ""
    async with pool.acquire() as c:
        if arg in ("on", "yes"):
            val = True
        elif arg in ("off", "no"):
            val = False
        else:
            cur = (
                await c.fetchval(
                    "SELECT lockwarns FROM ax_lock_settings WHERE chat_id=$1",
                    chat_id,
                )
                or False
            )
            await ax_mod.say(
                message,
                f"⚙️ <b>Lock warns</b>\n<blockquote>Currently <b>{'on' if cur else 'off'}</b>\n"
                "Use /lockwarns on or off</blockquote>",
                bot=bot,
            )
            return
        await c.execute(
            "INSERT INTO ax_lock_settings (chat_id, lockwarns) VALUES ($1,$2) "
            "ON CONFLICT (chat_id) DO UPDATE SET lockwarns=$2",
            chat_id,
            val,
        )
    core.invalidate(chat_id)
    await ax_mod.say(
        message,
        f"✅ <b>Lock warns {'on' if val else 'off'}</b>\n<blockquote>"
        f"{'Breaking a lock now adds a warning' if val else 'Locked messages are just deleted'}</blockquote>",
        bot=bot,
    )


@router.message(Command("allowlist"))
async def allowlist_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    args = [a.lower() for a in cmd_args(message)]
    async with pool.acquire() as c:
        for a in args:
            await c.execute(
                "INSERT INTO ax_allowlist (chat_id, item) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                chat_id,
                a,
            )
        rows = await c.fetch(
            "SELECT item FROM ax_allowlist WHERE chat_id=$1 ORDER BY item",
            chat_id,
        )
    core.invalidate(chat_id)
    lines = "\n".join(f"• {esc(r['item'])}" for r in rows) or "Empty"
    head = "✅ <b>Allowlist updated</b>" if args else "📋 <b>Allowlist</b>"
    await ax_mod.say(message, f"{head}\n<blockquote>{lines}</blockquote>", bot=bot)


@router.message(Command("rmallowlist"))
async def rmallowlist_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    args = [a.lower() for a in cmd_args(message)]
    if not args:
        await ax_mod.say(
            message,
            "💫 <b>Remove what?</b>\n<blockquote>Example: /rmallowlist @channel</blockquote>",
            bot=bot,
        )
        return
    chat_id = message.chat.id
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM ax_allowlist WHERE chat_id=$1 AND item = ANY($2::text[])",
            chat_id,
            args,
        )
    core.invalidate(chat_id)
    await ax_mod.say(
        message,
        "✅ <b>Removed from allowlist</b>\n<blockquote>Locks apply to those items again</blockquote>",
        bot=bot,
    )


@router.message(Command("rmallowlistall"))
async def rmallowlistall_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await _gate(message, bot):
        return
    chat_id = message.chat.id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ax_allowlist WHERE chat_id=$1", chat_id)
    core.invalidate(chat_id)
    await ax_mod.say(
        message,
        "✅ <b>Allowlist cleared</b>\n<blockquote>Everything on it is checked again</blockquote>",
        bot=bot,
    )


def register(dp) -> None:
    core.register(dp)
    if router.parent_router is None:
        dp.include_router(router)

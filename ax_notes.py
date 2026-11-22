"""Notes and filters (aiogram 3.x)."""
import logging
import re
import time
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

import ax_fill
from ax_common import ANON_ID, cmd_args, esc, get_bot_username, is_admin, right_label

log = logging.getLogger(__name__)
router = Router(name="ax_notes")

H = ParseMode.HTML
KINDS = ("photo", "video", "animation", "sticker", "voice", "audio", "document")

_DM_NOTE_RE = re.compile(r"^/start(?:@\w+)?\s+note_(-?\d+)_(.+)$")

_filter_cache: dict[int, tuple[float, list[dict]]] = {}
_note_names_cache: dict[int, tuple[float, set[str]]] = {}
_CACHE_TTL = 30.0
_CACHE_MAX = 5000
_middleware_registered = False


def _prune_cache(store: dict, now: float) -> None:
    if len(store) <= _CACHE_MAX:
        return
    expired = [cid for cid, (ts, _) in store.items() if now - ts >= _CACHE_TTL]
    for cid in expired:
        store.pop(cid, None)
    if len(store) > _CACHE_MAX:
        oldest = sorted(store.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 5]
        for cid, _ in oldest:
            store.pop(cid, None)


def invalidate(chat_id: int) -> None:
    _filter_cache.pop(chat_id, None)
    _note_names_cache.pop(chat_id, None)


async def _get_filters(pool: asyncpg.Pool, chat_id: int) -> list[dict]:
    now = time.time()
    hit = _filter_cache.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT trigger_text, content_type, text_content, file_id FROM ax_filters WHERE chat_id=$1",
            chat_id,
        )
    data = [dict(r) for r in rows]
    _prune_cache(_filter_cache, now)
    _filter_cache[chat_id] = (now, data)
    return data


async def _get_note_names(pool: asyncpg.Pool, chat_id: int) -> set[str]:
    now = time.time()
    hit = _note_names_cache.get(chat_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT name FROM ax_notes WHERE chat_id=$1", chat_id)
    names = {r["name"] for r in rows}
    _prune_cache(_note_names_cache, now)
    _note_names_cache[chat_id] = (now, names)
    return names


async def say(message: Message, text: str):
    return await message.reply(text, parse_mode=H)


async def admin_only(message: Message, bot: Bot) -> bool:
    chat = message.chat
    user = message.from_user
    if user is None:
        return False
    if chat.type == "private":
        await say(message, "\U0001F6E1\U0000FE0F <b>Groups only</b>\n<blockquote>Use this in your group as an admin \U00002728</blockquote>")
        return False
    if await is_admin(bot, chat.id, user.id, "can_change_info"):
        return True
    if user.id == ANON_ID:
        why = "I can't tell who an anonymous admin is. Send it from your own account, or an admin can turn on /anonadmin for this group \U00002728"
    elif await is_admin(bot, chat.id, user.id):
        why = "You are an admin, but you need the <b>%s</b> permission for this \U00002728" % right_label("can_change_info")
    else:
        why = "Use this in your group as an admin \U00002728"
    await say(message, "\U0001F6E1\U0000FE0F <b>Admins only</b>\n<blockquote>%s</blockquote>" % why)
    return False


def extract(reply: Message | None, rest: str) -> tuple[str, str | None, str | None]:
    if reply:
        for k in KINDS:
            obj = getattr(reply, k, None)
            if obj:
                fid = obj[-1].file_id if k == "photo" else obj.file_id
                text = None if k == "sticker" else (reply.caption or rest or None)
                return k, text, fid
        if reply.text and not rest:
            return "text", reply.text, None
    return "text", rest or None, None


async def deliver(
    msg: Message,
    bot: Bot,
    ctype: str,
    text: str | None,
    fid: str | None,
    raw: bool = False,
    cid: int | None = None,
) -> None:
    kb = None
    kw: dict[str, Any] = {}
    flags: dict[str, bool] = {}

    if text and not raw:
        body, rows, flags = ax_fill.parse(ax_fill.pick(text))
        text = ax_fill.fill(body, msg.from_user, msg.chat)
        target_cid = cid or msg.chat.id
        un = await get_bot_username(bot)
        kb = ax_fill.keyboard(
            rows,
            note_url=(lambda n: f"https://t.me/{un}?start=note_{target_cid}_{n.lower()}") if un else None,
        )
        if flags.get("nonotif"):
            kw["disable_notification"] = True
        if flags.get("protect"):
            kw["protect_content"] = True

    async def go(pm):
        if ctype == "text":
            await msg.reply(text or "…", parse_mode=pm, reply_markup=kb, **kw)
        elif ctype == "sticker":
            await msg.reply_sticker(fid, reply_markup=kb, **kw)
        else:
            extra: dict[str, Any] = {}
            if ctype in ("photo", "video", "animation") and flags.get("mediaspoiler"):
                extra["has_spoiler"] = True
            await getattr(msg, "reply_" + ctype)(
                fid,
                caption=text,
                parse_mode=pm,
                reply_markup=kb,
                **kw,
                **extra,
            )

    try:
        await go(None if raw else H)
    except TelegramBadRequest:
        if raw:
            raise
        await go(None)


def make_save(table: str, col: str, label: str, usage: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        if not await admin_only(message, bot) or message.from_user is None:
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await say(message, f"📝 <b>Save a {label}</b>\n<blockquote>{usage}</blockquote>")
            return
        key = parts[1].lower()[:50]
        rest = parts[2].strip() if len(parts) > 2 else ""
        ctype, text, fid = extract(message.reply_to_message, rest)
        if ctype == "text" and not text:
            await say(message, f"💫 <b>Add some content</b>\n<blockquote>{usage}</blockquote>")
            return
        chat_id = message.chat.id
        async with pool.acquire() as c:
            await c.execute(
                f"INSERT INTO {table} (chat_id, {col}, content_type, text_content, file_id, created_by) "
                f"VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (chat_id, {col}) DO UPDATE SET "
                f"content_type=$3, text_content=$4, file_id=$5, created_by=$6",
                chat_id,
                key,
                ctype,
                text,
                fid,
                message.from_user.id,
            )
        invalidate(chat_id)
        await say(message, f"✅ <b>{label.title()} saved</b>\n<blockquote>🏷️ <b>{esc(key)}</b></blockquote>")

    return cmd


def make_list(table: str, col: str, label: str, prefix: str, addcmd: str):
    async def cmd(message: Message, pool: asyncpg.Pool) -> None:
        async with pool.acquire() as c:
            rows = await c.fetch(
                f"SELECT {col} AS k FROM {table} WHERE chat_id=$1 ORDER BY {col}",
                message.chat.id,
            )
        if not rows:
            await say(
                message,
                f"📭 <b>No {label}s yet</b>\n<blockquote>Admins can add one with /{addcmd} ✨</blockquote>",
            )
            return
        lines = "\n".join(f"• {prefix}{esc(r['k'])}" for r in rows)
        await say(message, f"📒 <b>Saved {label}s</b>\n<blockquote>{lines}</blockquote>")

    return cmd


def make_delete(table: str, col: str, label: str, cmdname: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        if not await admin_only(message, bot):
            return
        args = cmd_args(message)
        if not args:
            await say(message, f"💫 <b>Which {label}?</b>\n<blockquote>Usage: /{cmdname} name</blockquote>")
            return
        chat_id = message.chat.id
        async with pool.acquire() as c:
            r = await c.execute(
                f"DELETE FROM {table} WHERE chat_id=$1 AND {col}=$2",
                chat_id,
                args[0].lower(),
            )
        invalidate(chat_id)
        ok = r.endswith("1")
        await say(
            message,
            "🗑️ <b>Removed</b>" if ok else f"🔎 <b>Not found</b>\n<blockquote>No such {label} here 💫</blockquote>",
        )

    return cmd


def make_clear(table: str, label: str):
    async def cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
        if not await admin_only(message, bot):
            return
        chat_id = message.chat.id
        async with pool.acquire() as c:
            await c.execute(f"DELETE FROM {table} WHERE chat_id=$1", chat_id)
        invalidate(chat_id)
        await say(message, f"🗑️ <b>All {label}s cleared</b>")

    return cmd


def match_trigger(trig: str, low: str) -> bool:
    if trig.startswith("exact:"):
        return low.strip() == trig[6:]
    if trig.startswith("prefix:"):
        return low.startswith(trig[7:])
    return re.search(r"(?<!\w)" + re.escape(trig) + r"(?!\w)", low) is not None


def split_tags(text: str | None) -> tuple[str | None, dict[str, bool]]:
    t = text or ""
    f: dict[str, bool] = {}
    for k in ("admin", "private", "noprivate"):
        f[k] = ("{" + k + "}") in t
        t = t.replace("{" + k + "}", "")
    return (t.strip() or None), f


async def serve(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    r,
    name: str,
    raw: bool = False,
) -> None:
    user = message.from_user
    chat = message.chat
    if user is None:
        return
    text, f = split_tags(r["text_content"])
    if raw:
        text = r["text_content"]
    if f["admin"] and not await is_admin(bot, chat.id, user.id):
        await say(message, "🔒 <b>Admins only</b>\n<blockquote>This note is for admins ✨</blockquote>")
        return
    private = f["private"]
    if not private and not f["noprivate"]:
        async with pool.acquire() as c:
            private = bool(
                await c.fetchval("SELECT private FROM ax_note_settings WHERE chat_id=$1", chat.id)
            )
    param = "note_%d_%s" % (chat.id, name)
    if private and chat.type != "private" and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", param):
        un = await get_bot_username(bot)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📖 Open note", url=f"https://t.me/{un}?start={param}")]
            ]
        )
        await message.reply(
            "📝 <b>%s</b>\n<blockquote>Tap the button to read this note in private ✨</blockquote>"
            % esc(name),
            parse_mode=H,
            reply_markup=kb,
        )
        return
    await deliver(message, bot, r["content_type"], text, r["file_id"], raw=raw)


@router.message(Command("get"))
async def get_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if message.chat.type == "private":
        await say(message, "⚠️ <b>Groups only</b>\n<blockquote>Notes live inside groups 💫</blockquote>")
        return
    args = cmd_args(message)
    if not args:
        await say(message, "💫 <b>Which note?</b>\n<blockquote>Usage: /get name\nSee all with /notes</blockquote>")
        return
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT content_type, text_content, file_id FROM ax_notes WHERE chat_id=$1 AND name=$2",
            message.chat.id,
            args[0].lower(),
        )
    if not r:
        await say(message, "🔎 <b>Not found</b>\n<blockquote>No such note here. Try /notes 💫</blockquote>")
        return
    try:
        await serve(
            message,
            bot,
            pool,
            r,
            args[0].lower(),
            raw=len(args) > 1 and args[1].lower() == "noformat",
        )
    except Exception as e:
        await say(message, f"⚠️ <b>Couldn't send it</b>\n<blockquote>{esc(e)}</blockquote>")


@router.message(Command("privatenotes"))
async def privatenotes_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not await admin_only(message, bot):
        return
    chat = message.chat
    args = cmd_args(message)
    arg = args[0].lower() if args else ""
    async with pool.acquire() as c:
        if arg in ("on", "yes", "true", "off", "no", "false"):
            val = arg in ("on", "yes", "true")
            await c.execute(
                "INSERT INTO ax_note_settings (chat_id, private) VALUES ($1,$2) "
                "ON CONFLICT (chat_id) DO UPDATE SET private=$2",
                chat.id,
                val,
            )
            if val:
                await say(
                    message,
                    "🔐 <b>Private notes on</b>\n<blockquote>Notes now open in DM with a button ✨</blockquote>",
                )
            else:
                await say(
                    message,
                    "📢 <b>Private notes off</b>\n<blockquote>Notes are sent right here in the group ✨</blockquote>",
                )
            return
        cur = bool(await c.fetchval("SELECT private FROM ax_note_settings WHERE chat_id=$1", chat.id))
    await say(
        message,
        "🔐 <b>Private notes: %s</b>\n<blockquote>Use /privatenotes on or off</blockquote>"
        % ("on" if cur else "off"),
    )


@router.message(F.chat.type == "private", F.text.regexp(r"^/start(?:@\w+)?\s+note_(-?\d+)_(.+)$"))
async def dm_note(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    user = message.from_user
    m = _DM_NOTE_RE.match(message.text or "")
    if not m or user is None:
        return
    cid, name = int(m.group(1)), m.group(2).lower()[:50]
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT content_type, text_content, file_id FROM ax_notes "
            "WHERE chat_id=$1 AND name=$2",
            cid,
            name,
        )
    if not r:
        await message.reply(
            "🔎 <b>Not found</b>\n<blockquote>That note is gone or never existed ✨</blockquote>",
            parse_mode=H,
        )
        return
    text, f = split_tags(r["text_content"])
    if f["admin"] and not await is_admin(bot, cid, user.id):
        await message.reply(
            "🔒 <b>Admins only</b>\n<blockquote>This note is for admins ✨</blockquote>",
            parse_mode=H,
        )
        return
    try:
        await deliver(message, bot, r["content_type"], text, r["file_id"], cid=cid)
    except Exception:
        pass


async def _run_hash_watcher(msg: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not msg.text or not msg.text.startswith("#"):
        return
    parts = msg.text[1:].split(None, 1)
    if not parts:
        return
    word = parts[0].lower()[:50]
    names = await _get_note_names(pool, msg.chat.id)
    if word not in names:
        return
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT content_type, text_content, file_id FROM ax_notes "
            "WHERE chat_id=$1 AND name=$2",
            msg.chat.id,
            word,
        )
    if not r:
        return
    try:
        await serve(msg, bot, pool, r, word)
    except Exception:
        pass


async def _run_filter_watcher(msg: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    if not msg.text or msg.text.startswith("/"):
        return
    rows = await _get_filters(pool, msg.chat.id)
    if not rows:
        return
    low = msg.text.lower()
    for r in rows:
        if match_trigger(r["trigger_text"], low):
            try:
                await deliver(msg, bot, r["content_type"], r["text_content"], r["file_id"])
            except Exception:
                pass
            break


class NotesFiltersMiddleware(BaseMiddleware):
    """Non-blocking watcher for group filters and #notename triggers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type in ("group", "supergroup")
            and event.text
            and event.from_user is not None
            and not event.from_user.is_bot
        ):
            bot: Bot | None = data.get("bot")
            pool: asyncpg.Pool | None = data.get("pool")
            if bot is not None and pool is not None:
                if not event.text.startswith("/"):
                    await _run_filter_watcher(event, bot, pool)
                if event.text.startswith("#"):
                    await _run_hash_watcher(event, bot, pool)
        return await handler(event, data)


_N = ("ax_notes", "name", "note")
_F = ("ax_filters", "trigger_text", "filter")

router.message(Command("save"))(
    make_save(*_N, "/save name your text\nOr reply to any media with /save name")
)
router.message(Command("notes"))(make_list(*_N, "/get ", "save"))
router.message(Command("clear"))(make_delete(*_N, "clear"))
router.message(Command("clearallnotes"))(make_clear("ax_notes", "note"))
router.message(Command("filter"))(
    make_save(*_F, "/filter word your reply\nOr reply to any media with /filter word")
)
router.message(Command("filters"))(make_list(*_F, "", "filter"))
router.message(Command("stop"))(make_delete(*_F, "stop"))
router.message(Command("clearfilters"))(make_clear("ax_filters", "filter"))


def register(dp) -> None:
    global _middleware_registered
    if not _middleware_registered:
        _middleware_registered = True
        dp.message.outer_middleware(NotesFiltersMiddleware())
    if router.parent_router is None:
        dp.include_router(router)

# --- notes admin patch applied ---

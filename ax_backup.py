"""Group settings backup: /export /import /reset (aiogram 3.x)."""
import io
import json

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ax_common import esc, is_admin

router = Router(name="ax_backup")
H = ParseMode.HTML

TABLES = [
    "ax_settings", "ax_notes", "ax_filters", "ax_rules", "ax_locks",
    "ax_lock_settings", "ax_blocklist", "ax_blocklist_settings",
    "ax_allowlist", "ax_raid_settings", "ax_report_settings",
    "ax_pin_settings", "ax_captcha_settings", "ax_cleancmd",
    "ax_cleanservice", "ax_disabled", "ax_disable_settings",
    "ax_log_settings", "ax_misc_settings", "ax_note_settings",
    "ax_warn_settings",
]
SKIP = {"chat_id", "created_at", "raid_until", "last_welcome_id"}


async def _say(message: Message, text: str, **kw):
    return await message.reply(text, parse_mode=H, **kw)


async def _is_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return m.status == ChatMemberStatus.CREATOR


def _invalidate_all_caches(chat_id: int) -> None:
    """Clear per-chat settings caches after import/reset so changes take effect immediately."""
    for mod_name, fn_or_dict in (
        ("ax_lockcore", "invalidate"),
        ("ax_blocklist", "invalidate"),
        ("ax_notes", "invalidate"),
        ("ax_rules", "invalidate"),
        ("ax_pins", "invalidate"),
        ("ax_reports", "invalidate"),
        ("ax_log", "invalidate"),
    ):
        try:
            mod = __import__(mod_name)
            fn = getattr(mod, fn_or_dict, None)
            if callable(fn):
                fn(chat_id)
        except Exception:
            pass
    for mod_name, attr in (
        ("ax_settings", "_cache"),
        ("ax_raid", "_SC"),
        ("ax_captcha", "_SC"),
        ("ax_cleancmd", "_cache"),
        ("ax_cleanservice", "_cache"),
        ("ax_disable", "_cache"),
        ("ax_warn", "_SCACHE"),
    ):
        try:
            mod = __import__(mod_name)
            store = getattr(mod, attr, None)
            if isinstance(store, dict):
                store.pop(chat_id, None)
        except Exception:
            pass


@router.message(Command("export"))
async def export_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type not in ("group", "supergroup"):
        await _say(message, "<b>Groups only</b>\n<blockquote>Use this inside a group ✨</blockquote>")
        return
    if not await is_admin(bot, chat.id, user.id, "can_change_info"):
        await _say(message, "🔒 <b>Admins only</b>\n<blockquote>Only admins can export ✨</blockquote>")
        return

    out = {}
    async with pool.acquire() as c:
        for t in TABLES:
            rows = await c.fetch("SELECT * FROM %s WHERE chat_id=$1" % t, chat.id)
            if rows:
                out[t] = [
                    {
                        k: v
                        for k, v in dict(r).items()
                        if k not in SKIP and (v is None or isinstance(v, (str, int, float, bool)))
                    }
                    for r in rows
                ]
    data = json.dumps({"rumi_backup": 1, "tables": out}, ensure_ascii=False, indent=1).encode("utf-8")
    await message.reply_document(
        document=BufferedInputFile(data, filename="rumi_backup_%d.json" % abs(chat.id)),
        caption="📦 <b>Backup ready</b>\n<blockquote>Reply to this file with /import in any group to load it 💫</blockquote>",
        parse_mode=H,
    )


@router.message(Command("import"))
async def import_cmd(message: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type not in ("group", "supergroup"):
        await _say(message, "<b>Groups only</b>\n<blockquote>Use this inside a group ✨</blockquote>")
        return
    if not await _is_creator(bot, chat.id, user.id):
        await _say(message, "👑 <b>Creator only</b>\n<blockquote>Only the group creator can import ✨</blockquote>")
        return

    doc = message.reply_to_message.document if message.reply_to_message else None
    if not doc or (doc.file_size or 0) > 1_000_000:
        await _say(
            message,
            "📎 <b>Reply to a backup file</b>\n<blockquote>Reply to a Rumi backup .json file with /import 💫</blockquote>",
        )
        return
    try:
        f = await bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await bot.download_file(f.file_path, destination=buf)
        d = json.loads(buf.getvalue().decode("utf-8"))
        tabs = d["tables"]
        assert d.get("rumi_backup") == 1 and isinstance(tabs, dict)
    except Exception:
        await _say(
            message,
            "⚠️ <b>Not a Rumi backup</b>\n<blockquote>That file could not be read ✨</blockquote>",
        )
        return

    done = 0
    try:
        async with pool.acquire() as c:
            async with c.transaction():
                for t in TABLES:
                    if t not in tabs:
                        continue
                    got = await c.fetch(
                        "SELECT column_name FROM information_schema.columns WHERE table_name=$1",
                        t,
                    )
                    cols = {r["column_name"] for r in got}
                    await c.execute("DELETE FROM %s WHERE chat_id=$1" % t, chat.id)
                    for row in tabs[t]:
                        keys = [k for k in row if k in cols and k not in SKIP]
                        names = ",".join(["chat_id"] + keys)
                        ph = ",".join("$%d" % (i + 1) for i in range(len(keys) + 1))
                        await c.execute(
                            "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT DO NOTHING" % (t, names, ph),
                            chat.id,
                            *[row[k] for k in keys],
                        )
                        done += 1
    except Exception as e:
        await _say(
            message,
            "⚠️ <b>Import failed</b>\n<blockquote>Nothing was changed: %s</blockquote>" % esc(str(e)[:150]),
        )
        return

    _invalidate_all_caches(chat.id)
    await _say(
        message,
        "✅ <b>Imported</b>\n<blockquote>%d entries loaded, %s 💫</blockquote>"
        % (done, esc(user.first_name or "friend")),
    )


@router.message(Command("reset"))
async def reset_cmd(message: Message, bot: Bot) -> None:
    chat = message.chat
    user = message.from_user
    if user is None:
        return
    if chat.type not in ("group", "supergroup"):
        await _say(message, "<b>Groups only</b>\n<blockquote>Use this inside a group ✨</blockquote>")
        return
    if not await _is_creator(bot, chat.id, user.id):
        await _say(message, "👑 <b>Creator only</b>\n<blockquote>Only the group creator can reset ✨</blockquote>")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Yes, reset", callback_data="axrst_yes"),
                InlineKeyboardButton(text="Cancel", callback_data="axrst_no"),
            ]
        ]
    )
    await _say(
        message,
        "⚠️ <b>Reset all settings?</b>\n<blockquote>This clears notes, filters, rules, locks and every Rumi setting of this group. Wallets and warns stay safe.</blockquote>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("axrst_"))
async def reset_cb(query: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if query.message is None:
        await query.answer()
        return
    chat = query.message.chat
    if not await _is_creator(bot, chat.id, query.from_user.id):
        await query.answer("Only the group creator can press this", show_alert=True)
        return
    await query.answer()
    if query.data == "axrst_no":
        try:
            await query.message.edit_text("Cancelled, nothing changed ✨")
        except Exception:
            pass
        return
    async with pool.acquire() as c:
        async with c.transaction():
            for t in TABLES:
                await c.execute("DELETE FROM %s WHERE chat_id=$1" % t, chat.id)
    _invalidate_all_caches(chat.id)
    try:
        await query.message.edit_text("✅ Group settings were reset 💫")
    except Exception:
        pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

"""Account recovery & merge: /setpass, /mpass, /cpass, /merge (aiogram 3.x, bot_id aware)."""
import hashlib
import html
import logging
import os

import asyncpg
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

import bot_context
import config
import services
from ax_common import cmd_args
from bot_context import BotContext, get_brand

log = logging.getLogger(__name__)
router = Router(name="ax_recovery")
H = ParseMode.HTML


def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    _, actual_hash = _hash_password(password, salt_hex)
    return actual_hash == expected_hash


async def _ensure_recovery_tables(conn) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS ax_recovery_auth ("
        "bot_id BIGINT NOT NULL DEFAULT 0, "
        "user_id BIGINT NOT NULL, "
        "password_salt TEXT NOT NULL, "
        "password_hash TEXT NOT NULL, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (bot_id, user_id))"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS ax_account_merges ("
        "id BIGSERIAL PRIMARY KEY, "
        "bot_id BIGINT NOT NULL DEFAULT 0, "
        "old_user_id BIGINT NOT NULL, "
        "new_user_id BIGINT NOT NULL, "
        "merged_cash BIGINT NOT NULL DEFAULT 0, "
        "merged_gems BIGINT NOT NULL DEFAULT 0, "
        "merged_premium BOOLEAN NOT NULL DEFAULT FALSE, "
        "merged_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "UNIQUE (bot_id, old_user_id))"
    )


async def _is_telegram_account_deleted(bot: Bot, old_user_id: int) -> bool:
    """Return True if Telegram reports the account as deleted/inaccessible as a live user."""
    try:
        chat_obj = await bot.get_chat(old_user_id)
        if not getattr(chat_obj, "first_name", None) and not getattr(chat_obj, "username", None):
            return True
        return False
    except TelegramBadRequest as exc:
        msg = str(exc).lower()
        if "user not found" in msg or "chat not found" in msg or "user is deactivated" in msg:
            return True
        return False
    except TelegramAPIError:
        return False


@router.message(Command("setpass"))
async def setpass_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    args = cmd_args(message)
    if not args or len(args[0]) < 4:
        await message.reply(
            "🔐 <b>How to use /setpass</b>\n"
            "<blockquote>Set a recovery password (min 4 characters):\n"
            "<code>/setpass MySecretPass123</code>\n"
            "<i>Tip: Use this in DM so others in a group don't see your password.</i></blockquote>",
            parse_mode=H,
        )
        return

    pwd = args[0]
    salt_hex, hash_hex = _hash_password(pwd)
    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_recovery_tables(conn)
            await conn.execute(
                "INSERT INTO ax_recovery_auth (bot_id, user_id, password_salt, password_hash, updated_at) "
                "VALUES ($1, $2, $3, $4, now()) "
                "ON CONFLICT (bot_id, user_id) DO UPDATE SET "
                "password_salt = EXCLUDED.password_salt, "
                "password_hash = EXCLUDED.password_hash, "
                "updated_at = now()",
                bid,
                user.id,
                salt_hex,
                hash_hex,
            )

    if message.chat.type != "private":
        try:
            await message.delete()
        except Exception:
            pass
    await message.reply(
        "🔐 <b>Recovery Password Saved</b>\n"
        "<blockquote>Your recovery password is now active for account recovery. "
        "Use <code>/mpass</code> in DM to view status or <code>/cpass</code> to change it.</blockquote>",
        parse_mode=H,
    )


@router.message(Command("mpass"))
async def mpass_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    bname = html.escape(get_brand(bid).name)

    async with pool.acquire() as conn:
        await _ensure_recovery_tables(conn)
        row = await conn.fetchrow(
            "SELECT updated_at FROM ax_recovery_auth WHERE bot_id=$1 AND user_id=$2",
            bid,
            user.id,
        )

    if row:
        stamp = row["updated_at"].strftime("%Y-%m-%d %H:%M UTC")
        status_line = f"✅ Recovery password is <b>SET</b>\n🕒 Last updated: <code>{stamp}</code>"
    else:
        status_line = "❌ No recovery password set yet. Use <code>/setpass &lt;password&gt;</code>."

    if message.chat.type != "private":
        try:
            await bot.send_message(
                user.id,
                f"🔐 <b>{bname} Recovery Password Status</b>\n<blockquote>{status_line}</blockquote>",
                parse_mode=H,
            )
            await message.reply(
                "📩 <b>Sent to your DM</b>\n<blockquote>Check your private chat with me for recovery password details.</blockquote>",
                parse_mode=H,
            )
        except Exception:
            await message.reply(
                "📩 <b>Open my DM first</b>\n<blockquote>Start a private chat with me and send <code>/mpass</code> there.</blockquote>",
                parse_mode=H,
            )
        return

    await message.reply(
        f"🔐 <b>{bname} Recovery Password Info</b>\n"
        f"<blockquote>"
        f"{status_line}\n"
        f"🔄 <b>Change Password:</b> <code>/cpass &lt;old_pass&gt; &lt;new_pass&gt;</code>"
        f"</blockquote>",
        parse_mode=H,
    )


@router.message(Command("cpass"))
async def cpass_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    args = cmd_args(message)
    if len(args) < 2 or len(args[1]) < 4:
        await message.reply(
            "🔐 <b>How to use /cpass</b>\n"
            "<blockquote>Change your recovery password:\n"
            "<code>/cpass &lt;old_password&gt; &lt;new_password&gt;</code> (new password min 4 chars)</blockquote>",
            parse_mode=H,
        )
        return

    old_pwd, new_pwd = args[0], args[1]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_recovery_tables(conn)
            row = await conn.fetchrow(
                "SELECT password_salt, password_hash FROM ax_recovery_auth WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bid,
                user.id,
            )
            if not row or not _verify_password(old_pwd, row["password_salt"], row["password_hash"]):
                await message.reply(
                    "❌ <b>Invalid Current Password</b>\n"
                    "<blockquote>Could not change password. Check your current password or set one with <code>/setpass</code>.</blockquote>",
                    parse_mode=H,
                )
                return
            salt_hex, hash_hex = _hash_password(new_pwd)
            await conn.execute(
                "UPDATE ax_recovery_auth SET password_salt=$3, password_hash=$4, updated_at=now() "
                "WHERE bot_id=$1 AND user_id=$2",
                bid,
                user.id,
                salt_hex,
                hash_hex,
            )

    if message.chat.type != "private":
        try:
            await message.delete()
        except Exception:
            pass
    await message.reply(
        "✅ <b>Recovery Password Changed</b>\n<blockquote>Your new recovery password is now active.</blockquote>",
        parse_mode=H,
    )


@router.message(Command("merge"))
async def merge_cmd(message: Message, bot: Bot, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    user = message.from_user
    if user is None:
        return
    bid = bot_ctx.bot_id
    sym = getattr(config, "CURRENCY_SYMBOL", "$")
    args = cmd_args(message)
    if len(args) < 2 or not args[0].lstrip("-").isdigit():
        await message.reply(
            "♻️ <b>How to use /merge</b>\n"
            "<blockquote>Recover coins, gems, and Premium from a deleted old account:\n"
            "<code>/merge &lt;old_user_id&gt; &lt;deleted_account_password&gt;</code>\n\n"
            "<i>• Only works for deleted accounts with a password set.\n"
            "• Merges coins, vault cash, gems &amp; Premium (XP/level/stats stay separate).</i></blockquote>",
            parse_mode=H,
        )
        return

    old_uid = int(args[0])
    pwd = args[1]
    if old_uid == user.id:
        await message.reply(
            "⚠️ <b>Same Account</b>\n<blockquote>You cannot merge an account into itself.</blockquote>",
            parse_mode=H,
        )
        return

    if not await _is_telegram_account_deleted(bot, old_uid):
        await message.reply(
            "⚠️ <b>Account Not Deleted</b>\n"
            "<blockquote><code>/merge</code> only works when the old Telegram account is actually deleted.</blockquote>",
            parse_mode=H,
        )
        return

    await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_recovery_tables(conn)
            already = await conn.fetchval(
                "SELECT 1 FROM ax_account_merges WHERE bot_id=$1 AND old_user_id=$2",
                bid,
                old_uid,
            )
            if already:
                await message.reply(
                    "⚠️ <b>Already Merged</b>\n<blockquote>This old account has already been merged once.</blockquote>",
                    parse_mode=H,
                )
                return

            auth = await conn.fetchrow(
                "SELECT password_salt, password_hash FROM ax_recovery_auth WHERE bot_id=$1 AND user_id=$2",
                bid,
                old_uid,
            )
            if not auth or not _verify_password(pwd, auth["password_salt"], auth["password_hash"]):
                await message.reply(
                    "❌ <b>Verification Failed</b>\n<blockquote>Old user ID or recovery password is incorrect.</blockquote>",
                    parse_mode=H,
                )
                return

            old_w = await conn.fetchrow(
                "SELECT cash, vault, gems FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bid,
                old_uid,
            )
            new_w = await conn.fetchrow(
                "SELECT cash, vault, gems FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE",
                bid,
                user.id,
            )
            if new_w is None:
                await message.reply(
                    "⚠️ <b>Wallet Missing</b>\n<blockquote>Send /start first to initialize your wallet.</blockquote>",
                    parse_mode=H,
                )
                return

            move_cash = int(old_w["cash"] or 0) + int(old_w["vault"] or 0) if old_w else 0
            move_gems = int(old_w["gems"] or 0) if old_w else 0
            has_prem = bool(
                await conn.fetchval(
                    "SELECT 1 FROM ax_premium WHERE bot_id=$1 AND user_id=$2",
                    bid,
                    old_uid,
                )
            )

            before_cash = int(new_w["cash"])
            after_cash = before_cash + move_cash
            after_gems = int(new_w["gems"]) + move_gems

            await conn.execute(
                "UPDATE wallets SET cash=$1, gems=$2, updated_at=now() WHERE bot_id=$3 AND user_id=$4",
                after_cash,
                after_gems,
                bid,
                user.id,
            )
            if old_w:
                await conn.execute(
                    "UPDATE wallets SET cash=0, vault=0, gems=0, updated_at=now() WHERE bot_id=$1 AND user_id=$2",
                    bid,
                    old_uid,
                )
            if move_cash > 0:
                await services.insert_transaction(
                    conn,
                    user_id=user.id,
                    type_="ACCOUNT_MERGE",
                    currency="cash",
                    amount=move_cash,
                    balance_before=before_cash,
                    balance_after=after_cash,
                    related_user_id=old_uid,
                    source="merge_command",
                    bot_id=bid,
                )
            if has_prem:
                import ax_premium

                await ax_premium._grant(pool, user.id, bot_id=bid)

            await conn.execute(
                "INSERT INTO ax_account_merges "
                "(bot_id, old_user_id, new_user_id, merged_cash, merged_gems, merged_premium) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                bid,
                old_uid,
                user.id,
                move_cash,
                move_gems,
                has_prem,
            )

    if message.chat.type != "private":
        try:
            await message.delete()
        except Exception:
            pass

    await message.reply(
        f"✅ <b>ACCOUNT MERGED</b>\n"
        f"<blockquote>"
        f"👤 <b>From Old ID:</b> <code>{old_uid}</code>\n"
        f"💰 <b>Coins Added:</b> <code>+{sym}{move_cash:,}</code>\n"
        f"💎 <b>Gems Added:</b> <code>+{move_gems:,}</code>\n"
        f"💓 <b>Premium Transferred:</b> <b>{'Yes' if has_prem else 'No'}</b>\n"
        f"<i>XP &amp; levels remain untouched on your current account.</i>"
        f"</blockquote>",
        parse_mode=H,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

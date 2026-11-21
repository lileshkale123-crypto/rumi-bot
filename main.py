import asyncio
import importlib
import inspect
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import Response, TelegramType
from aiogram.types import TelegramObject, Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import bot_context
import db
import keepalive
import migrate_patch1
import migrate_patch2
import migrate_patch3
import migrate_patch5
import migrate_step1
import migrate_step2a
import migrate_step2b
import migrate_step2c
import migrate_step2d1

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

MAX_UPDATE_AGE = timedelta(minutes=10)
MAX_AUTO_RETRY_AFTER = 15.0

POLLING_UPDATES = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "purchased_paid_media",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "chat_boost",
    "removed_chat_boost",
]

MODULE_ORDER = [
    "ax_errors",
    "ax_perf",
    "ax_i18n",
    "quote_cache",
    "ax_track",
    "ax_watch",
    "ax_disable",
    "ax_ban",
    "ax_cleancmd",
    "ax_cleanservice",
    "ax_flood",
    "ax_settings",
    "ax_lockcore",
    "ax_blocklist",
    "ax_raid",
    "ax_captcha",
    "ax_afk",
    "ax_pins",
    "ax_addprompt",
    "ax_grouprew",
    "ax_channel",
    "ax_channel2",
    "ax_dmproxy",
    "autodelete",
    # Early /start deep-links, private state machines & callback overrides
    "ax_post",
    "ax_notes",
    "ax_rules",
    "ax_connect2",
    "ax_referral",
    "mafia_handlers",
    "ax_help",
    "ax_claimcheck",
    # Core economy, games & clans
    "ax_gamesmenu",
    "handlers",
    "economy_features",
    "bluff_handlers",
    "clans",
    "chat_events",
    # Feature modules
    "ax_welcome",
    "ax_welcome2",
    "ax_captcha2",
    "ax_connect",
    "ax_topics",
    "ax_actiontopic",
    "ax_lang",
    "ax_extras",
    "ax_donate",
    "ax_premium",
    "ax_recovery",
    "ax_givecoins",
    "ax_backup",
    "ax_love",
    "ax_social",
    "ax_meter",
    "ax_quote",
    "ax_qextra",
    "ax_menu",
    "ax_mod",
    "ax_mod2",
    "ax_warn",
    "ax_warn2",
    "ax_approve",
    "ax_locks",
    "ax_purge",
    "ax_admin2",
    "ax_adminpanel",
    "ax_adminpanel2",
    "ax_ownerpanel",
    "ax_cheat",
    "ax_funmode",
    "ax_kill",
    "ax_broadcast",
    "ax_leaderboard",
    "ax_toprich",
    "ax_topgroups",
    "ax_shop",
    "ax_gift",
    "ax_profile",
    "ax_tag",
    "ax_speak",
    "ax_kang",
    "ax_ping",
    "ax_log",
    "ax_misc3",
    "ax_truthdare",
    # Patch 2-5 modules (powers, daily tasks, friends/tags, arcade, events & dot-mod)
    "ax_powers",
    "ax_tasks",
    "ax_social_extras",
    "ax_arcade",
    "ax_events_mod",
    # Non-command text/media fallbacks
    "media_helper",
    "ax_reel",
    "ax_reports",
    "ai_handlers",
    "auto_react",
]


class IgnoreOldUpdatesMiddleware(BaseMiddleware):
    """Drop stale queued messages older than MAX_UPDATE_AGE after downtime."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and event.message and event.message.date:
            msg_date = event.message.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - msg_date > MAX_UPDATE_AGE:
                return None
        return await handler(event, data)


class RetryAfterRequestMiddleware(BaseRequestMiddleware):
    """Automatically back off on short Telegram flood waits and transient network glitches."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)

        for attempt in range(3):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                wait = float(getattr(exc, "retry_after", 1) or 1)
                if attempt == 2 or wait > MAX_AUTO_RETRY_AFTER:
                    raise
                log.warning(
                    "Telegram flood wait %.1fs on %s (attempt %d/3)",
                    wait,
                    type(method).__name__,
                    attempt + 1,
                )
                await asyncio.sleep(wait + 0.25)
            except TelegramNetworkError as exc:
                if attempt == 2:
                    raise
                log.warning(
                    "Transient Telegram network error on %s: %s (retrying)",
                    type(method).__name__,
                    exc,
                )
                await asyncio.sleep(0.5 * (attempt + 1))

        return await make_request(bot, method)


def _call_register(mod: Any, dp: Dispatcher, bot: Bot) -> None:
    reg_fn = getattr(mod, "register", None)
    if callable(reg_fn):
        params = list(inspect.signature(reg_fn).parameters.values())
        if len(params) >= 2:
            reg_fn(dp, bot)
        else:
            reg_fn(dp)

    router = getattr(mod, "router", None)
    if isinstance(router, Router) and router.parent_router is None:
        dp.include_router(router)


def register_modules(dp: Dispatcher, bot: Bot) -> list[Any]:
    """Import and register all migrated modules exactly once in priority order."""
    loaded: list[Any] = []
    seen: set[str] = set()
    for mod_name in MODULE_ORDER:
        if mod_name in seen:
            continue
        seen.add(mod_name)
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:
            log.warning("Could not import module %s: %s", mod_name, exc)
            continue

        try:
            _call_register(mod, dp, bot)
            loaded.append(mod)
        except Exception:
            log.exception("Failed registering module %s", mod_name)
    return loaded


async def on_startup(bot: Bot, dispatcher: Dispatcher) -> None:
    pool = await db.init_pool()
    dispatcher["pool"] = pool
    dispatcher["bot_data"]["pool"] = pool

    me = await bot.me()
    dispatcher["bot_username"] = me.username or ""
    dispatcher["bot_id"] = me.id

    main_ctx = bot_context.BotContext(
        bot_id=me.id,
        username=me.username or "",
        display_name="Rumi",
        bot_type="rumi",
        settings={},
    )
    bot_context.set_main_context(main_ctx)
    dispatcher["bot_ctx"] = main_ctx

    # Safe idempotent startup migrations (never crash startup on error)
    await migrate_step1.run_step1_migration(pool, me.id)
    await migrate_step2a.run_step2a_migration(pool, me.id)
    await migrate_step2b.run_step2b_migration(pool, me.id)
    await migrate_step2c.run_step2c_migration(pool, me.id)
    await migrate_step2d1.run_step2d1_migration(pool, me.id)
    await migrate_patch1.run_patch1_migration(pool, me.id)
    for _name, _mig, _fn in (
        ("patch2", migrate_patch2, "run_patch2_migration"),
        ("patch3", migrate_patch3, "run_patch3_migration"),
        ("patch5", migrate_patch5, "run_patch5_migration"),
    ):
        try:
            await getattr(_mig, _fn)(pool, me.id)
        except Exception:
            log.exception("Migration %s failed (bot continues)", _name)

    scheduler: AsyncIOScheduler = dispatcher["scheduler"]
    if not scheduler.running:
        scheduler.start()

    for mod_name, fn_name in (
        ("ax_ban", "_load"),
        ("ax_i18n", "load"),
        ("games", "refund_unfinished"),
    ):
        try:
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                res = await fn(pool)
                if mod_name == "games" and res:
                    log.info("Refunded %s unfinished Shadow Bluff game(s).", res)
        except Exception:
            log.exception("Startup hook %s.%s failed", mod_name, fn_name)

    for mod in dispatcher.get("_loaded_modules", []):
        hook = getattr(mod, "on_startup", None)
        if callable(hook):
            try:
                sig = inspect.signature(hook)
                if len(sig.parameters) >= 2:
                    await hook(bot, dispatcher)
                elif len(sig.parameters) == 1:
                    await hook(dispatcher)
                else:
                    await hook()
            except Exception:
                log.exception("Module %s.on_startup failed", mod.__name__)


async def on_shutdown(bot: Bot, dispatcher: Dispatcher) -> None:
    for mod in reversed(dispatcher.get("_loaded_modules", [])):
        hook = getattr(mod, "on_shutdown", None)
        if callable(hook):
            try:
                sig = inspect.signature(hook)
                if len(sig.parameters) >= 2:
                    await hook(bot, dispatcher)
                elif len(sig.parameters) == 1:
                    await hook(dispatcher)
                else:
                    await hook()
            except Exception:
                log.exception("Module %s.on_shutdown failed", mod.__name__)

    scheduler: AsyncIOScheduler | None = dispatcher.get("scheduler")
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)

    tasks: set[asyncio.Task] = dispatcher.get("background_tasks") or set()
    for task in list(tasks):
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    await db.close_pool()
    try:
        await bot.session.close()
    except Exception:
        pass


async def run_bot() -> None:
    token = os.environ["BOT_TOKEN"]
    main_bot_id = bot_context.parse_bot_id_from_token(token)
    init_ctx = bot_context.BotContext(
        bot_id=main_bot_id,
        username="",
        display_name="Rumi",
        bot_type="rumi",
        settings={},
    )
    bot_context.set_main_context(init_ctx)

    session = AiohttpSession(limit=256)
    session.middleware(RetryAfterRequestMiddleware())

    bot = Bot(token=token, session=session)

    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    dp = Dispatcher()
    dp["bot"] = bot
    dp["scheduler"] = scheduler
    dp["bot_data"] = {}
    dp["background_tasks"] = set()
    dp["bot_username"] = ""
    dp["bot_id"] = main_bot_id
    dp["bot_ctx"] = init_ctx

    dp.update.outer_middleware(IgnoreOldUpdatesMiddleware())
    dp.update.outer_middleware(bot_context.BotContextMiddleware())
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    dp["_loaded_modules"] = register_modules(dp, bot)

    keepalive.start()
    log.info("Rumi is running with aiogram 3.x (long polling). Press Ctrl+C to stop.")
    await dp.start_polling(
        bot,
        allowed_updates=POLLING_UPDATES,
        handle_signals=True,
    )


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()

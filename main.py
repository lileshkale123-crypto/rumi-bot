import logging
import os

from dotenv import load_dotenv
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationHandlerStop, TypeHandler

import bluff_handlers
import economy_features
import db
import games
import handlers

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


MAX_UPDATE_AGE = timedelta(minutes=10)


async def ignore_old_updates(update, context):
    msg = update.message
    if msg and msg.date and datetime.now(timezone.utc) - msg.date > MAX_UPDATE_AGE:
        raise ApplicationHandlerStop


async def post_init(application: Application) -> None:
    pool = await db.init_pool()
    application.bot_data["pool"] = pool
    import ax_ban
    await ax_ban._load(pool)
    refunded = await games.refund_unfinished(pool)
    if refunded:
        print(f"Refunded {refunded} unfinished Shadow Bluff game(s).")


def main() -> None:
    token = os.environ["BOT_TOKEN"]
    application = Application.builder().token(token).post_init(post_init).concurrent_updates(True).build()
    application.add_handler(TypeHandler(Update, ignore_old_updates), group=-1)

    application.add_handler(CommandHandler("start", handlers.start_command))
    application.add_handler(CommandHandler("wallet", handlers.wallet_command))
    application.add_handler(CommandHandler("claim", handlers.claim_command))
    application.add_handler(CommandHandler("pay", handlers.pay_command))
    application.add_handler(CommandHandler("guard", handlers.guard_command))
    application.add_handler(CommandHandler("rob", handlers.rob_command))
    application.add_handler(CommandHandler("rankings", handlers.rankings_command))
    application.add_handler(CommandHandler("bluff", bluff_handlers.bluff_command))

    application.add_handler(CallbackQueryHandler(handlers.wallet_command, pattern="^wallet:refresh$"))
    application.add_handler(CallbackQueryHandler(handlers.claim_command, pattern="^claim$"))
    application.add_handler(CallbackQueryHandler(handlers.rankings_callback, pattern="^rankings:"))
    application.add_handler(CallbackQueryHandler(bluff_handlers.bluff_callback, pattern="^bluff:"))
    application.add_handler(CallbackQueryHandler(handlers.features_callback, pattern="^features$"))
    application.add_handler(CallbackQueryHandler(handlers.games_callback, pattern="^games$"))
    application.add_handler(CallbackQueryHandler(handlers.noop_callback, pattern="^noop$"))

    from telegram.ext import MessageHandler, filters
    import media_helper
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.ANIMATION | filters.VIDEO | filters.Document.ALL)
        & filters.ChatType.PRIVATE,
        media_helper.file_id_handler,
    ))

    import mafia_handlers
    for _cmd, _fn in (("vault", economy_features.vault_command),
                      ("deposit", economy_features.deposit_command),
                      ("withdraw", economy_features.withdraw_command),
                      ("work", economy_features.work_command),
                      ("spin", economy_features.spin_command),
                      ("mafiatop", economy_features.mafiatop_command)):
        application.add_handler(CommandHandler(_cmd, _fn))
    application.add_handler(CommandHandler("mafia", mafia_handlers.mafia_command))
    application.add_handler(CallbackQueryHandler(
        mafia_handlers.lobby_callback, pattern="^mafia:(join|start|cancel):"
    ))

    from telegram.ext import MessageHandler, filters
    application.add_handler(MessageHandler(
        filters.Regex(r'^/start mafia_') & filters.ChatType.PRIVATE,
        mafia_handlers.dm_start_handler,
    ), group=-1)

    application.add_handler(CallbackQueryHandler(
        mafia_handlers.night_callback, pattern="^mn:"
    ))

    application.add_handler(CallbackQueryHandler(
        mafia_handlers.vote_callback, pattern="^mv:"
    ))

    application.add_handler(CallbackQueryHandler(
        mafia_handlers.kamikaze_callback, pattern="^mk:"
    ))

    application.add_handler(CommandHandler("stopmafia", mafia_handlers.stop_command))

    from telegram.ext import MessageHandler, filters
    import ai_handlers
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, ai_handlers.ai_chat
    ), group=5)
    import auto_react
    auto_react.register(application)

    import ax_track
    ax_track.register(application)
    import ax_welcome
    ax_welcome.register(application)
    import ax_notes
    ax_notes.register(application)
    import ax_extras
    ax_extras.register(application)
    import ax_donate
    ax_donate.register(application)
    import ax_givecoins
    ax_givecoins.register(application)
    import ax_backup
    ax_backup.register(application)
    import ax_topics
    ax_topics.register(application)
    import ax_connect
    ax_connect.register(application)
    import ax_connect2
    ax_connect2.register(application)
    import ax_actiontopic
    ax_actiontopic.register(application)
    import ax_lang
    ax_lang.register(application)
    import ax_settings
    ax_settings.register(application)
    import ax_afk
    ax_afk.register(application)
    import ax_love
    ax_love.register(application)
    import ax_quote
    ax_quote.register(application)
    import ax_menu
    ax_menu.register(application)
    import ax_menu
    ax_menu.register(application)
    import ax_mod
    ax_mod.register(application)
    import ax_speak
    ax_speak.register(application)
    import ax_topgroups
    ax_topgroups.register(application)
    import ax_tag
    ax_tag.register(application)
    import clans
    clans.register(application)
    import ax_kang
    ax_kang.register(application)
    import ax_ping
    ax_ping.register(application)
    import ax_errors
    ax_errors.register(application)
    import ax_reel
    ax_reel.register(application)
    import ax_profile
    ax_profile.register(application)
    import ax_mod2
    ax_mod2.register(application)
    import ax_warn
    ax_warn.register(application)
    import ax_warn2
    ax_warn2.register(application)
    import ax_adminpanel
    ax_adminpanel.register(application)
    import ax_adminpanel2
    ax_adminpanel2.register(application)
    import ax_watch
    ax_watch.register(application)
    import ax_cheat
    ax_cheat.register(application)
    import ax_funmode
    ax_funmode.register(application)
    import ax_kill
    ax_kill.register(application)
    import ax_broadcast
    ax_broadcast.register(application)
    import ax_ban
    ax_ban.register(application)
    import ax_leaderboard
    ax_leaderboard.register(application)
    import ax_referral
    ax_referral.register(application)
    import ax_purge
    ax_purge.register(application)
    import ax_admin2
    ax_admin2.register(application)
    import ax_help
    ax_help.register(application)
    import ax_approve
    ax_approve.register(application)
    import ax_locks
    ax_locks.register(application)
    import ax_blocklist
    ax_blocklist.register(application)
    import ax_raid
    ax_raid.register(application)
    import ax_flood
    ax_flood.register(application)
    import ax_rules
    ax_rules.register(application)
    import ax_reports
    ax_reports.register(application)
    import ax_pins
    ax_pins.register(application)
    import ax_welcome2
    ax_welcome2.register(application)
    import ax_captcha
    ax_captcha.register(application)
    import ax_captcha2
    ax_captcha2.register(application)
    import ax_cleancmd
    ax_cleancmd.register(application)
    import ax_cleanservice
    ax_cleanservice.register(application)
    import ax_disable
    ax_disable.register(application)
    import ax_log
    ax_log.register(application)
    import ax_misc3
    ax_misc3.register(application)
    import ax_dmproxy
    ax_dmproxy.register(application)

    print("Rumi is running (long polling). Press Ctrl+C to stop.")
    import autodelete
    autodelete.install(application)
    application.run_polling()


if __name__ == "__main__":
    main()

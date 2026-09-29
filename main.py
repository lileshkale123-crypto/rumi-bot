import logging
import os

from dotenv import load_dotenv
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import bluff_handlers
import db
import games
import handlers

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def post_init(application: Application) -> None:
    pool = await db.init_pool()
    application.bot_data["pool"] = pool
    refunded = await games.refund_unfinished(pool)
    if refunded:
        print(f"Refunded {refunded} unfinished Shadow Bluff game(s).")


def main() -> None:
    token = os.environ["BOT_TOKEN"]
    application = Application.builder().token(token).post_init(post_init).build()

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

    print("Rumi is running (long polling). Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()

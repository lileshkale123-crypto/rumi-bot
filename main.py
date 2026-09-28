import logging
import os

from dotenv import load_dotenv
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import db
import handlers

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def post_init(application: Application) -> None:
    pool = await db.init_pool()
    application.bot_data["pool"] = pool


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

    application.add_handler(CallbackQueryHandler(handlers.wallet_command, pattern="^wallet:refresh$"))
    application.add_handler(CallbackQueryHandler(handlers.claim_command, pattern="^claim$"))
    application.add_handler(CallbackQueryHandler(handlers.rankings_callback, pattern="^rankings:"))
    application.add_handler(CallbackQueryHandler(handlers.features_callback, pattern="^features$"))
    application.add_handler(CallbackQueryHandler(handlers.games_callback, pattern="^games$"))
    application.add_handler(CallbackQueryHandler(handlers.noop_callback, pattern="^noop$"))

    print("Rumi is running (long polling). Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()

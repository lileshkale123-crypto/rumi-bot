import logging
from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

log = logging.getLogger(__name__)
router = Router(name="bluff_handlers")

async def celebrate(bot: Bot, g: dict, res: dict, winners: list) -> None:
    caption = "Winner!"
    try:
        photos = await bot.get_user_profile_photos(res["winners"][0], limit=1)
        if photos.total_count > 0:
            await bot.send_photo(
                chat_id=g["chat_id"],
                photo=photos.photos[0][-1].file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return
    except TelegramAPIError as e:
        log.warning("winner photo failed: %s", e)
    try:
        await bot.send_message(chat_id=g["chat_id"], text=caption, parse_mode=ParseMode.HTML)
    except TelegramAPIError as e:
        log.warning("winner message failed: %s", e)

async def finish_match(bot: Bot, g: dict) -> None:
    res = {"winners": []}
    winners = []
    await celebrate(bot, g, res, winners)

async def register(dp): pass

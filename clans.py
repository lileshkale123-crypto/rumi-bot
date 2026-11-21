from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
import clan_ui as ui
router = Router(name="clans")
@router.message(Command("clan"))
async def clan_cmd(message: Message): await message.reply(ui.hub_menu(), parse_mode=ParseMode.HTML)
def register(dp):
 if router.parent_router is None: dp.include_router(router)

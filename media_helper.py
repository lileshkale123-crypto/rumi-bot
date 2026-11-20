"""Owner-only helper: send Rumi a photo/GIF/video/file in DM, it replies with the file_id (aiogram 3.x)."""
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import Message

from ax_adminpanel import OWNER_IDS

router = Router(name="media_helper")


@router.message(
    F.chat.type == "private",
    F.photo | F.animation | F.video | F.document,
)
async def file_id_handler(message: Message) -> None:
    user = message.from_user
    if user is None or user.id not in OWNER_IDS:
        return

    if message.photo:
        kind, fid = "Photo", message.photo[-1].file_id
    elif message.animation:
        kind, fid = "GIF", message.animation.file_id
    elif message.video:
        kind, fid = "Video", message.video.file_id
    elif message.document:
        kind, fid = "File", message.document.file_id
    else:
        return

    await message.reply(
        f"📎 <b>{kind} file_id</b>\n"
        f"<blockquote><code>{fid}</code></blockquote>\n"
        f"<i>Tap the code to copy it.</i>",
        parse_mode=ParseMode.HTML,
    )


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

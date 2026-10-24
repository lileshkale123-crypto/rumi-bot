# Owner-only helper: send Rumi a photo/GIF in DM, it replies with the file_id.
from telegram import Update
from telegram.ext import ContextTypes

from ax_adminpanel import OWNER_IDS


async def file_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or update.effective_chat.type != "private":
        return
    if user.id not in OWNER_IDS:
        return

    if msg.photo:
        kind, fid = "Photo", msg.photo[-1].file_id
    elif msg.animation:
        kind, fid = "GIF", msg.animation.file_id
    elif msg.video:
        kind, fid = "Video", msg.video.file_id
    elif msg.document:
        kind, fid = "File", msg.document.file_id
    else:
        return

    await msg.reply_html(
        f"📎 <b>{kind} file_id</b>\n"
        f"<blockquote><code>{fid}</code></blockquote>\n"
        f"<i>Tap the code to copy it.</i>"
    )

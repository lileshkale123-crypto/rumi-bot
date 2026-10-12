import asyncio
import html
import logging
import os
import re
import shutil
import tempfile

import yt_dlp
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)

SITES = (
    r"instagram\.com", r"youtube\.com", r"youtu\.be", r"tiktok\.com",
    r"facebook\.com", r"fb\.watch", r"x\.com", r"twitter\.com",
    r"pinterest\.com", r"pin\.it", r"reddit\.com", r"threads\.net",
)
URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*(?:" + "|".join(SITES) + r")/\S+", re.I
)
MAX_BYTES = 49 * 1024 * 1024
_sem = asyncio.Semaphore(2)

FAIL = (
    "😿 <b>Couldn't grab that one</b>\n"
    "<blockquote>It may be private, over 10 minutes, "
    "or bigger than 50 MB.</blockquote>"
)


def _download(url, folder):
    opts = {
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "format": "bv*[height<=720]+ba/b[height<=720]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "max_filesize": MAX_BYTES,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [os.path.join(folder, f) for f in os.listdir(folder)]
    if not files:
        return None
    return max(files, key=os.path.getsize)


async def watcher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not msg.text or not user or user.is_bot:
        return
    log.warning('REEL saw text: %s', msg.text[:60])
    found = URL_RE.search(msg.text)
    if not found:
        return
    url = found.group(0)
    status = await msg.reply_text(
        "🔎 <b>Fetching media…</b>", parse_mode=ParseMode.HTML
    )
    folder = tempfile.mkdtemp(prefix="reel_")
    try:
        async with _sem:
            path = await asyncio.to_thread(_download, url, folder)
        if not path or os.path.getsize(path) > MAX_BYTES:
            await status.edit_text(FAIL, parse_mode=ParseMode.HTML)
            return
        name = html.escape(user.first_name or "friend")
        caption = (
            "<b>🎬 Here you go!</b>\n"
            f"<blockquote>Requested by <b>{name}</b>\n"
            f"Downloaded by @{context.bot.username}</blockquote>"
        )
        with open(path, "rb") as f:
            await msg.reply_video(
                video=f,
                caption=caption,
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
            )
        await status.delete()
    except Exception:
        log.exception("REELERR download failed")
        try:
            await status.edit_text(FAIL, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, watcher), group=-6
    )

"""Auto video/reel downloader for social media links using yt-dlp (aiogram 3.x)."""
import asyncio
import html
import logging
import os
import re
import shutil
import tempfile

import yt_dlp
from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, Message

from ax_common import fire, get_bot_username

log = logging.getLogger(__name__)
router = Router(name="ax_reel")

SITES = (
    r"instagram\.com",
    r"tiktok\.com",
    r"facebook\.com",
    r"fb\.watch",
    r"x\.com",
    r"twitter\.com",
    r"pinterest\.com",
    r"pin\.it",
    r"reddit\.com",
    r"threads\.net",
)
URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*(?:" + "|".join(SITES) + r")/\S+",
    re.I,
)
MAX_BYTES = 49 * 1024 * 1024
_sem = asyncio.Semaphore(2)

FAIL = (
    "😿 <b>Couldn't grab that one</b>\n"
    "<blockquote>It may be private, over 10 minutes, "
    "or bigger than 50 MB.</blockquote>"
)


def _download(url: str, folder: str) -> str | None:
    opts = {
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "format": "b[height<=720]/b/bv*+ba",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_BYTES,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
    ]
    if not files:
        return None
    return max(files, key=os.path.getsize)


async def _fetch_and_send(message: Message, bot: Bot, url: str) -> None:
    user = message.from_user
    if user is None:
        return
    status = await message.reply("🔎 <b>Fetching media…</b>", parse_mode=ParseMode.HTML)
    folder = tempfile.mkdtemp(prefix="reel_")
    try:
        async with _sem:
            path = await asyncio.to_thread(_download, url, folder)
        if not path or not os.path.isfile(path) or os.path.getsize(path) > MAX_BYTES:
            await status.edit_text(FAIL, parse_mode=ParseMode.HTML)
            return
        name = html.escape(user.first_name or "friend")
        bot_username = await get_bot_username(bot)
        caption = (
            "<b>🎬 Here you go!</b>\n"
            f"<blockquote>Requested by <b>{name}</b>\n"
            f"Downloaded by @{bot_username}</blockquote>"
        )
        await message.reply_video(
            video=FSInputFile(path),
            caption=caption,
            parse_mode=ParseMode.HTML,
            supports_streaming=True,
            request_timeout=120,
        )
        try:
            await status.delete()
        except Exception:
            pass
    except Exception:
        log.exception("REELERR download failed")
        try:
            await status.edit_text(FAIL, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    finally:
        await asyncio.to_thread(shutil.rmtree, folder, True)


@router.message(F.text & ~F.text.startswith("/") & F.text.regexp(URL_RE))
async def watcher(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not message.text or user is None or user.is_bot:
        return
    found = URL_RE.search(message.text)
    if not found:
        return
    fire(_fetch_and_send(message, bot, found.group(0)))


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

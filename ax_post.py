"""Owner-only /post: build a channel post with a photo, text and COLORED buttons (aiogram 3.x).

Flow (in a private chat with Rumi, owners only):
    /post  ->  send photo/video/GIF (or /skip)  ->  send text  ->  send buttons
           ->  preview  ->  [Send to channel] or forward the preview yourself.

Button format, one per line (" | " puts two on the same row):
    🚀 START RUMI - https://t.me/im_rumibot?start=start - green
    ➕ ADD TO YOUR GROUP - https://t.me/im_rumibot?startgroup=true - blue
Colors: green, blue, red.  No color = Telegram's default grey.

Channel: set POST_CHANNEL in your environment (like @yourchannel or -100123...),
or set DEFAULT_CHANNEL below, or type it:  /post @yourchannel
"""
import html
import os
import re

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from ax_adminpanel import OWNER_IDS
from ax_common import btn, cmd_args

router = Router(name="ax_post")

H = ParseMode.HTML
DEFAULT_CHANNEL = ""  # e.g. "@yourchannel" (leave empty to use POST_CHANNEL / /post @channel)
CAPTION_LIMIT = 1024
STYLES = {"green": "success", "blue": "primary", "red": "danger"}

_drafts: dict[int, dict] = {}

BUTTON_HELP = (
    "🔘 <b>Send your buttons</b>, one per line:\n"
    "<blockquote><code>🚀 START RUMI - https://t.me/im_rumibot?start=start - green\n"
    "➕ ADD TO YOUR GROUP - https://t.me/im_rumibot?startgroup=true - blue</code></blockquote>\n"
    "Colors: <code>green</code> <code>blue</code> <code>red</code>\n"
    "Two buttons on one row: separate them with <code> | </code>\n"
    "Send /skip for a post with no buttons."
)


class _HasDraftFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        u = message.from_user
        return (
            u is not None
            and u.id in OWNER_IDS
            and message.chat.type == "private"
            and u.id in _drafts
        )


def parse_buttons(raw: str) -> InlineKeyboardMarkup:
    """'Text - url - color' lines -> InlineKeyboardMarkup. Raises ValueError on bad input."""
    rows = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for part in line.split(" | "):
            bits = part.strip().split(" - ")
            color = None
            if len(bits) >= 3 and bits[-1].strip().lower() in STYLES:
                color = bits.pop().strip().lower()
            if len(bits) < 2:
                raise ValueError(f"Can't read: {part.strip()}")
            url = bits[-1].strip()
            label = " - ".join(bits[:-1]).strip()
            if url.startswith(("t.me/", "telegram.me/")):
                url = "https://" + url
            if not label or not re.match(r"^(https?://|tg://)\S+$", url):
                raise ValueError(f"Bad label or link: {part.strip()}")
            row.append(btn(label, url=url, style=STYLES[color] if color else None))
        rows.append(row)
    if not rows:
        raise ValueError("No buttons found.")
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_post(bot: Bot, chat_id: int | str, d: dict):
    markup = d.get("markup")
    kind, fid, text = d.get("kind"), d.get("file_id"), d["text"]
    if kind == "photo":
        return await bot.send_photo(chat_id, fid, caption=text, parse_mode=H, reply_markup=markup)
    if kind == "video":
        return await bot.send_video(chat_id, fid, caption=text, parse_mode=H, reply_markup=markup)
    if kind == "animation":
        return await bot.send_animation(chat_id, fid, caption=text, parse_mode=H, reply_markup=markup)
    return await bot.send_message(chat_id, text, parse_mode=H, reply_markup=markup)


@router.message(Command("post"), F.chat.type == "private")
async def post_cmd(message: Message) -> None:
    u = message.from_user
    if u is None or u.id not in OWNER_IDS:
        return
    args = cmd_args(message)
    arg = args[0] if args else None
    _drafts[u.id] = {"step": "media", "channel": arg}
    await message.reply(
        "📸 Send the <b>photo / video / GIF</b> for the post.\nOr send /skip for a text-only post. /cancel to stop.",
        parse_mode=H,
    )


@router.message(_HasDraftFilter())
async def on_message(message: Message, bot: Bot) -> None:
    u = message.from_user
    if u is None:
        return
    d = _drafts.get(u.id)
    if not d:
        return

    text = (message.text or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

    if cmd == "/cancel":
        _drafts.pop(u.id, None)
        await message.reply("Cancelled.")
        return

    step = d["step"]

    if step == "media":
        if cmd == "/skip":
            d["kind"], d["file_id"] = None, None
        elif message.photo:
            d["kind"], d["file_id"] = "photo", message.photo[-1].file_id
        elif message.video:
            d["kind"], d["file_id"] = "video", message.video.file_id
        elif message.animation:
            d["kind"], d["file_id"] = "animation", message.animation.file_id
        else:
            await message.reply("Please send a photo, video or GIF, or /skip.")
            return
        d["step"] = "text"
        await message.reply("✍️ Now send the <b>post text</b>.", parse_mode=H)
        return

    if step == "text":
        if not message.text or cmd:
            await message.reply("Please send the post text as a normal message.")
            return
        if d.get("kind") and len(message.text) > CAPTION_LIMIT:
            await message.reply(
                f"⚠️ With a photo/video the text can be at most {CAPTION_LIMIT} characters "
                f"(yours is {len(message.text)}). Send a shorter text."
            )
            return
        d["text"] = message.html_text
        d["step"] = "buttons"
        await message.reply(
            BUTTON_HELP,
            parse_mode=H,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    if step == "buttons":
        if cmd == "/skip":
            d["markup"] = None
        else:
            try:
                d["markup"] = parse_buttons(message.text or "")
            except ValueError as e:
                await message.reply(
                    f"❌ {e}\n\nFormat: Text - link - green",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
        d["step"] = "confirm"
        try:
            await _send_post(bot, message.chat.id, d)
        except Exception as e:
            d["step"] = "buttons"
            await message.reply(
                f"❌ Telegram rejected this post: {html.escape(str(e))}",
                parse_mode=H,
            )
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📢 Send to channel", callback_data="axpost:send")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="axpost:no")],
            ]
        )
        await message.reply(
            "👆 <b>Preview.</b> Tap <b>Send to channel</b>, or forward the preview yourself.",
            parse_mode=H,
            reply_markup=kb,
        )


@router.callback_query(F.data.startswith("axpost:"))
async def on_button(query: CallbackQuery, bot: Bot) -> None:
    if query.from_user.id not in OWNER_IDS:
        await query.answer()
        return
    d = _drafts.get(query.from_user.id)
    if not d or d.get("step") != "confirm":
        await query.answer("This draft has expired. Send /post again.", show_alert=True)
        return
    if query.data == "axpost:no":
        _drafts.pop(query.from_user.id, None)
        await query.answer("Cancelled")
        if query.message:
            try:
                await query.message.edit_text("Cancelled.")
            except Exception:
                pass
        return

    channel: int | str = d.get("channel") or os.environ.get("POST_CHANNEL") or DEFAULT_CHANNEL
    if not channel:
        await query.answer(
            "No channel set. Use /post @yourchannel, or set POST_CHANNEL.",
            show_alert=True,
        )
        return
    if re.fullmatch(r"-?\d+", str(channel)):
        channel = int(channel)

    try:
        await _send_post(bot, channel, d)
    except Exception as e:
        await query.answer("Failed to post", show_alert=True)
        if query.message:
            await query.message.reply(
                f"❌ Couldn't post to <code>{html.escape(str(channel))}</code>: {html.escape(str(e))}\n"
                "Make sure Rumi is an admin of the channel with permission to post.",
                parse_mode=H,
            )
        return

    _drafts.pop(query.from_user.id, None)
    await query.answer("Posted ✅")
    if query.message:
        try:
            await query.message.edit_text("✅ Posted to the channel.")
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

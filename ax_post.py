"""Owner-only /post: build a channel post with a photo, text and COLORED buttons.

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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from ax_adminpanel import OWNER_IDS

H = ParseMode.HTML
DEFAULT_CHANNEL = ""          # e.g. "@yourchannel" (leave empty to use POST_CHANNEL / /post @channel)
CAPTION_LIMIT = 1024
STYLES = {"green": "success", "blue": "primary", "red": "danger"}
KEY = "axpost"                # key inside context.user_data

BUTTON_HELP = (
    "🔘 <b>Send your buttons</b>, one per line:\n"
    "<blockquote><code>🚀 START RUMI - https://t.me/im_rumibot?start=start - green\n"
    "➕ ADD TO YOUR GROUP - https://t.me/im_rumibot?startgroup=true - blue</code></blockquote>\n"
    "Colors: <code>green</code> <code>blue</code> <code>red</code>\n"
    "Two buttons on one row: separate them with <code> | </code>\n"
    "Send /skip for a post with no buttons."
)


def parse_buttons(raw):
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
            extra = {"api_kwargs": {"style": STYLES[color]}} if color else {}
            row.append(InlineKeyboardButton(label, url=url, **extra))
        rows.append(row)
    if not rows:
        raise ValueError("No buttons found.")
    return InlineKeyboardMarkup(rows)


def _owner(update):
    u = update.effective_user
    return (
        u is not None
        and u.id in OWNER_IDS
        and update.effective_chat is not None
        and update.effective_chat.type == "private"
    )


async def _send_post(bot, chat_id, d):
    markup = d.get("markup")
    kind, fid, text = d.get("kind"), d.get("file_id"), d["text"]
    if kind == "photo":
        return await bot.send_photo(chat_id, fid, caption=text, parse_mode=H, reply_markup=markup)
    if kind == "video":
        return await bot.send_video(chat_id, fid, caption=text, parse_mode=H, reply_markup=markup)
    if kind == "animation":
        return await bot.send_animation(chat_id, fid, caption=text, parse_mode=H, reply_markup=markup)
    return await bot.send_message(chat_id, text, parse_mode=H, reply_markup=markup)


async def post_cmd(update, context):
    if not _owner(update):
        return
    arg = context.args[0] if context.args else None
    context.user_data[KEY] = {"step": "media", "channel": arg}
    await update.message.reply_text(
        "📸 Send the <b>photo / video / GIF</b> for the post.\nOr send /skip for a text-only post. /cancel to stop.",
        parse_mode=H,
    )
    raise ApplicationHandlerStop


async def on_message(update, context):
    """Runs before every other handler. Only acts while an owner is mid-/post."""
    if not _owner(update):
        return
    d = context.user_data.get(KEY)
    if not d:
        return
    m = update.message
    if m is None:
        return
    text = (m.text or "").strip()
    cmd = text.split("@")[0].lower() if text.startswith("/") else ""

    if cmd == "/cancel":
        context.user_data.pop(KEY, None)
        await m.reply_text("Cancelled.")
        raise ApplicationHandlerStop
    if cmd == "/post":
        return  # handled by post_cmd

    step = d["step"]

    if step == "media":
        if cmd == "/skip":
            d["kind"], d["file_id"] = None, None
        elif m.photo:
            d["kind"], d["file_id"] = "photo", m.photo[-1].file_id
        elif m.video:
            d["kind"], d["file_id"] = "video", m.video.file_id
        elif m.animation:
            d["kind"], d["file_id"] = "animation", m.animation.file_id
        else:
            await m.reply_text("Please send a photo, video or GIF, or /skip.")
            raise ApplicationHandlerStop
        d["step"] = "text"
        await m.reply_text("✍️ Now send the <b>post text</b>.", parse_mode=H)
        raise ApplicationHandlerStop

    if step == "text":
        if not m.text or cmd:
            await m.reply_text("Please send the post text as a normal message.")
            raise ApplicationHandlerStop
        if d.get("kind") and len(m.text) > CAPTION_LIMIT:
            await m.reply_text(
                f"⚠️ With a photo/video the text can be at most {CAPTION_LIMIT} characters "
                f"(yours is {len(m.text)}). Send a shorter text.",
            )
            raise ApplicationHandlerStop
        d["text"] = m.text_html
        d["step"] = "buttons"
        await m.reply_text(BUTTON_HELP, parse_mode=H, disable_web_page_preview=True)
        raise ApplicationHandlerStop

    if step == "buttons":
        if cmd == "/skip":
            d["markup"] = None
        else:
            try:
                d["markup"] = parse_buttons(m.text or "")
            except ValueError as e:
                await m.reply_text(f"❌ {e}\n\nFormat: Text - link - green", disable_web_page_preview=True)
                raise ApplicationHandlerStop
        d["step"] = "confirm"
        try:
            await _send_post(context.bot, m.chat_id, d)
        except Exception as e:
            d["step"] = "buttons"
            await m.reply_text(f"❌ Telegram rejected this post: {html.escape(str(e))}", parse_mode=H)
            raise ApplicationHandlerStop
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Send to channel", callback_data="axpost:send")],
            [InlineKeyboardButton("❌ Cancel", callback_data="axpost:no")],
        ])
        await m.reply_text(
            "👆 <b>Preview.</b> Tap <b>Send to channel</b>, or forward the preview yourself.",
            parse_mode=H, reply_markup=kb,
        )
        raise ApplicationHandlerStop


async def on_button(update, context):
    q = update.callback_query
    if q.from_user.id not in OWNER_IDS:
        await q.answer()
        return
    d = context.user_data.get(KEY)
    if not d or d.get("step") != "confirm":
        await q.answer("This draft has expired. Send /post again.", show_alert=True)
        return
    if q.data == "axpost:no":
        context.user_data.pop(KEY, None)
        await q.answer("Cancelled")
        await q.message.edit_text("Cancelled.")
        return
    channel = d.get("channel") or os.environ.get("POST_CHANNEL") or DEFAULT_CHANNEL
    if not channel:
        await q.answer(
            "No channel set. Use /post @yourchannel, or set POST_CHANNEL.", show_alert=True
        )
        return
    if re.fullmatch(r"-?\d+", str(channel)):
        channel = int(channel)
    try:
        await _send_post(context.bot, channel, d)
    except Exception as e:
        await q.answer("Failed to post", show_alert=True)
        await q.message.reply_text(
            f"❌ Couldn't post to <code>{html.escape(str(channel))}</code>: {html.escape(str(e))}\n"
            "Make sure Rumi is an admin of the channel with permission to post.",
            parse_mode=H,
        )
        return
    context.user_data.pop(KEY, None)
    await q.answer("Posted ✅")
    await q.message.edit_text("✅ Posted to the channel.")


def register(app):
    # group -1 so we run before media_helper (which would otherwise grab your photo).
    app.add_handler(CommandHandler("post", post_cmd, filters=filters.ChatType.PRIVATE), group=-1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, on_message), group=-1)
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^axpost:"))

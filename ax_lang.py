"""Language choice: /setlang (English, Hindi) (aiogram 3.x)."""
import asyncpg
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ax_common import esc

router = Router(name="ax_lang")
H = ParseMode.HTML
LANGS = {"en": "English", "hi": "हिन्दी"}

TXT = {
    "en": {
        "pick": "🌐 <b>Choose your language</b>\n<blockquote>%s, pick the language you like. Your choice is saved just for you 💫</blockquote>",
        "done": "✅ <b>Language saved</b>\n<blockquote>%s, Rumi will use English with you ✨</blockquote>",
    },
    "hi": {
        "pick": "🌐 <b>अपनी भाषा चुनें</b>\n<blockquote>%s, अपनी पसंद की भाषा चुनिए। आपकी पसंद सिर्फ आपके लिए सेव होती है 💫</blockquote>",
        "done": "✅ <b>भाषा सेव हो गई</b>\n<blockquote>%s, अब Rumi आपसे हिन्दी में बात करेगी ✨</blockquote>",
    },
}


async def get_lang(pool: asyncpg.Pool, user_id: int) -> str:
    try:
        async with pool.acquire() as c:
            v = await c.fetchval("SELECT lang FROM ax_user_lang WHERE user_id=$1", user_id)
    except Exception:
        return "en"
    return v if v in LANGS else "en"


@router.message(Command("setlang"))
async def setlang_cmd(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    if user is None:
        return
    cur = await get_lang(pool, user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=name, callback_data="axlg_" + code)
                for code, name in LANGS.items()
            ]
        ]
    )
    await message.reply(
        TXT[cur]["pick"] % esc(user.first_name or "friend"),
        parse_mode=H,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("axlg_"))
async def setlang_cb(query: CallbackQuery, pool: asyncpg.Pool) -> None:
    code = (query.data or "")[5:]
    if code not in LANGS:
        await query.answer()
        return
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ax_user_lang (user_id, lang) VALUES ($1,$2) "
            "ON CONFLICT (user_id) DO UPDATE SET lang=$2",
            query.from_user.id,
            code,
        )
    import ax_i18n

    ax_i18n.set_lang(query.from_user.id, code)
    await query.answer()
    if query.message:
        try:
            await query.message.edit_text(
                TXT[code]["done"] % esc(query.from_user.first_name or "friend"),
                parse_mode=H,
            )
        except Exception:
            pass


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

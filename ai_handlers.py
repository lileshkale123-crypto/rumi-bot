"""AI chat message handler for Rumi (aiogram 3.x)."""
import logging
import re
import time
from collections import OrderedDict

import asyncpg
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message

import ai_engine
import ai_store
import ax_common
import config

log = logging.getLogger(__name__)
router = Router(name="ai_handlers")

_MAX_SEEN = 5000
_MAX_AI_MSGS = 2000
_MAX_LAST_REPLY = 5000

_last_reply: OrderedDict[int, float] = OrderedDict()
_seen_msgs: OrderedDict[tuple[int, int], None] = OrderedDict()
_ai_msgs: OrderedDict[tuple[int, int], None] = OrderedDict()  # (chat_id, message_id) of Rumi's chat replies

_trigger = re.compile(r"\b(" + "|".join(map(re.escape, config.AI_TRIGGER_WORDS)) + r")\b", re.I)
_greet = re.compile(r"^\s*(" + "|".join(config.AI_GREETINGS) + r")\b", re.I)


def _remember_key(store: OrderedDict, key, limit: int) -> None:
    store[key] = None
    store.move_to_end(key)
    while len(store) > limit:
        store.popitem(last=False)


def _set_last_reply(chat_id: int, ts: float) -> None:
    _last_reply[chat_id] = ts
    _last_reply.move_to_end(chat_id)
    while len(_last_reply) > _MAX_LAST_REPLY:
        _last_reply.popitem(last=False)


async def _ai_chat_inner(
    msg: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_username: str,
) -> None:
    user = msg.from_user
    chat = msg.chat
    if not msg.text or user is None or user.is_bot:
        return

    text = msg.text.strip()[:1000]
    if not text:
        return

    private = chat.type == "private"
    direct = private
    if not direct:
        rt = msg.reply_to_message
        uname = (bot_username or await ax_common.get_bot_username(bot)).lower()
        if (
            rt
            and rt.from_user
            and rt.from_user.id == bot.id
            and (chat.id, rt.message_id) in _ai_msgs
        ):
            direct = True
        elif _trigger.search(text):
            direct = True
        elif uname and f"@{uname}" in text.lower():
            direct = True

    if not direct:
        return

    msg_key = (chat.id, msg.message_id)
    if msg_key in _seen_msgs:
        return
    _remember_key(_seen_msgs, msg_key, _MAX_SEEN)

    now = time.monotonic()
    if now - _last_reply.get(chat.id, 0.0) < config.AI_FLOOD_SECONDS:
        return

    _set_last_reply(chat.id, now)
    if not await ai_store.use_quota(pool, user.id):
        await msg.reply(
            f"we've chatted {config.AI_DAILY_LIMIT} times today 🌙 come back after midnight (IST)!"
        )
        return
    _set_last_reply(chat.id, time.monotonic())

    try:
        await bot.send_chat_action(chat.id, ChatAction.TYPING)
    except Exception:
        pass

    prompt = text if private else f"{user.first_name or 'Someone'}: {text}"
    reply = None
    try:
        history = await ai_store.get_history(pool, chat.id)
        reply = await ai_engine.chat(history, prompt)
    except Exception as e:
        log.warning("ai chat failed: %s", e)

    if not reply:
        await msg.reply("my brain is a little busy rn 🌸 try again in a moment")
        return

    await ai_store.save_turn(pool, chat.id, prompt, reply)
    sent = await msg.reply(reply[:4000])
    _remember_key(_ai_msgs, (chat.id, sent.message_id), _MAX_AI_MSGS)


@router.message(F.text & ~F.text.startswith("/"))
async def ai_chat(
    message: Message,
    bot: Bot,
    pool: asyncpg.Pool,
    bot_username: str = "",
) -> None:
    # Spawn as background task so slow LLM calls never block other updates
    ax_common.fire(_ai_chat_inner(message, bot, pool, bot_username))


async def on_shutdown() -> None:
    await ai_engine.close_client()


def register(dp) -> None:
    if router.parent_router is None:
        dp.include_router(router)

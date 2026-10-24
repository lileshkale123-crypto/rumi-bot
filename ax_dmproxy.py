"""DM commands act on the connected group (notes and filters first)."""
from types import SimpleNamespace
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import btn, esc, is_admin

H = ParseMode.HTML
CMDS = {"save", "get", "notes", "clear", "clearallnotes",
        "filter", "filters", "stop", "clearfilters",
        "lock", "unlock", "locks", "rules", "setrules", "resetrules",
        "warnings", "warnlimit", "warnmode", "warntime",
        "welcome", "setwelcome", "resetwelcome",
        "goodbye", "setgoodbye", "resetgoodbye",
        "setflood", "flood", "settings"}


class _Proxy:
    def __init__(self, upd, chat):
        self._u = upd
        self.effective_chat = chat

    def __getattr__(self, name):
        return getattr(self._u, name)


async def _connected(context, user_id):
    try:
        async with context.bot_data["pool"].acquire() as c:
            r = await c.fetchrow(
                "SELECT chat_id FROM ax_connections WHERE user_id=$1", user_id)
        if r and r["chat_id"]:
            return r["chat_id"]
    except Exception:
        pass
    return None


def _wrap(orig):
    async def inner(update, context):
        chat = update.effective_chat
        if not chat or chat.type != "private":
            return await orig(update, context)
        user, msg = update.effective_user, update.effective_message
        cid = await _connected(context, user.id)
        if not cid:
            await msg.reply_text(
                "\U0001F517 <b>Connect a group first</b>\n"
                "<blockquote>Send /connect inside your group and tap the button. "
                "After that, these commands work here and change that group \u2728</blockquote>",
                parse_mode=H)
            return
        if not await is_admin(context.bot, cid, user.id):
            await msg.reply_text(
                "\U0001F512 <b>Admins only</b>\n"
                "<blockquote>You are no longer an admin of the connected group. "
                "Use /disconnect or /connect again \u2728</blockquote>", parse_mode=H)
            return
        fake = SimpleNamespace(id=cid, type="supergroup", title=None,
                               is_forum=False, username=None)
        return await orig(_Proxy(update, fake), context)
    inner._dmw = True
    return inner


class _QMsg:
    def __init__(self, m, chat):
        self._m = m
        self.chat = chat

    def __getattr__(self, name):
        return getattr(self._m, name)


class _Query:
    def __init__(self, q, chat):
        self._q = q
        self.message = _QMsg(q.message, chat)

    def __getattr__(self, name):
        return getattr(self._q, name)


def _wrap_cb(orig):
    async def inner(update, context):
        q = update.callback_query
        if not q or not q.message or q.message.chat.type != "private":
            return await orig(update, context)
        cid = await _connected(context, q.from_user.id)
        if not cid:
            await q.answer("Send /connect in your group first", show_alert=True)
            return
        if not await is_admin(context.bot, cid, q.from_user.id):
            await q.answer("You are no longer an admin there", show_alert=True)
            return
        fake = SimpleNamespace(id=cid, type="supergroup", title=None,
                               is_forum=False, username=None)

        class _U:
            callback_query = _Query(q, fake)
            effective_chat = fake

            def __getattr__(self, name):
                return getattr(update, name)

        return await orig(_U(), context)
    inner._dmw = True
    return inner


def register(app):
    n = 0
    for h in app.handlers.get(0, []):
        if (isinstance(h, CommandHandler) and (set(h.commands) & CMDS)
                and not getattr(h.callback, "_dmw", False)):
            h.callback = _wrap(h.callback)
            h.callback._dmw = True
            n += 1
    from telegram.ext import CallbackQueryHandler
    for h in app.handlers.get(0, []):
        if (isinstance(h, CallbackQueryHandler)
                and getattr(h.pattern, "pattern", "") == "^axs:"
                and not getattr(h.callback, "_dmw", False)):
            h.callback = _wrap_cb(h.callback)
            n += 1
    print("dm proxy wrapped:", n)


"""Multi-language i18n engine (aiogram 3.x middleware, per-bot isolated).

Supports:
- Per-bot isolation: USER_LANGS[(bot_id, user_id)] and CHAT_LANGS[(bot_id, chat_id)].
- Group default language (`ax_chat_lang`) taking priority for all group messages, games,
  timers, buttons, and callback pop-up alerts.
- Personal user language (`ax_user_lang`) for private DM chats and personal alerts.
- Drop-in `lang_<code>.json` packs (v2 structured `{exact, patterns, phrases}` or legacy flat dict).
- Translates messages, captions, InlineKeyboardMarkup buttons (preserving custom button styles),
  AnswerCallbackQuery pop-up alerts, and SendInvoice labels.
"""
import contextvars
import html
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable

import asyncpg
from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageCaption,
    EditMessageReplyMarkup,
    EditMessageText,
    SendAnimation,
    SendAudio,
    SendDocument,
    SendInvoice,
    SendMessage,
    SendPhoto,
    SendVideo,
    SendVoice,
    TelegramMethod,
)
from aiogram.methods.base import Response, TelegramType
from aiogram.types import InlineKeyboardMarkup, LabeledPrice, TelegramObject, Update

import bot_context

log = logging.getLogger(__name__)

# Active language maps keyed by (bot_id, entity_id) so clones never share language state
USER_LANGS: dict[tuple[int, int], str] = {}   # (bot_id, user_id) -> lang code (non-"en")
CHAT_LANGS: dict[tuple[int, int], str] = {}   # (bot_id, chat_id) -> lang code (non-"en")
HI_USERS: set[tuple[int, int]] = set()        # (bot_id, user_id) Hindi users

# Per-update context: (chat_id, chat_type, user_id)
CUR_CTX: contextvars.ContextVar[tuple[int | None, str | None, int | None]] = contextvars.ContextVar(
    "axi_ctx",
    default=(None, None, None),
)
CUR: contextvars.ContextVar[int | None] = contextvars.ContextVar("axi_user", default=None)

# Loaded language packs: code -> {"exact": dict, "whole_pats": list, "line_pats": list, "phrases": list}
_PACKS: dict[str, dict[str, Any]] = {}
_loaded_files = False
_registered = False

_PH_LEGACY = re.compile(r"%s|%d|\{[a-z_0-9]*\}")
_REPL_TOKEN = re.compile(r"\{(t?)(\d+)\}")
_HTML_TAG_SPLIT = re.compile(r"(<[^>]+>)")
_SKIP_BTN_RE = re.compile(r"^[\d\s/#:◀▶•.\-\[\]A-Z]+$")
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_ENTITY_RE = re.compile(r"&(?:#x[0-9a-fA-F]+|#\d+|amp|lt|gt|quot);")


def _compile_legacy_pack(raw: dict[str, str]) -> dict[str, Any]:
    exact: dict[str, str] = {}
    whole_pats: list[tuple[re.Pattern, str]] = []
    for k, v in raw.items():
        if not v or not isinstance(v, str):
            continue
        exact[k] = v
        pct = any(t in ("%s", "%d") for t in _PH_LEGACY.findall(k))
        kk = k.replace("%%", "%") if pct else k
        hv = v.replace("%%", "%") if pct else v
        toks = _PH_LEGACY.findall(kk)
        if not toks or len(_PH_LEGACY.sub("", kk).strip()) < 12:
            continue
        rx, pos = "", 0
        for m in _PH_LEGACY.finditer(kk):
            rx += re.escape(kk[pos : m.start()]) + "(.*?)"
            pos = m.end()
        rx += re.escape(kk[pos:])
        idx = 0

        def _to_numbered(m: re.Match) -> str:
            nonlocal idx
            idx += 1
            return "{" + str(idx) + "}"

        hv_num = _PH_LEGACY.sub(_to_numbered, hv)
        try:
            whole_pats.append((re.compile("^" + rx + "$", re.S), hv_num))
        except re.error:
            pass
    return {"exact": exact, "whole_pats": whole_pats, "line_pats": [], "phrases": []}


def _compile_v2_pack(data: dict[str, Any]) -> dict[str, Any]:
    exact: dict[str, str] = {}
    for k, v in (data.get("exact") or {}).items():
        if isinstance(k, str) and isinstance(v, str) and v:
            exact[k] = v

    whole_pats: list[tuple[re.Pattern, str]] = []
    line_pats: list[tuple[re.Pattern, str]] = []
    for item in data.get("patterns") or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        pat_str, repl = item
        try:
            rx = re.compile(pat_str)
        except re.error as exc:
            log.warning("i18n invalid regex %r: %s", pat_str, exc)
            continue
        if r"\n" in pat_str or r"[\s\S]" in pat_str:
            whole_pats.append((rx, repl))
        else:
            line_pats.append((rx, repl))

    phrases: list[tuple[str, str]] = [
        (src, dst)
        for src, dst in (data.get("phrases") or [])
        if isinstance(src, str) and isinstance(dst, str) and src
    ]
    return {
        "exact": exact,
        "whole_pats": whole_pats,
        "line_pats": line_pats,
        "phrases": phrases,
    }


def _load_files() -> None:
    global _loaded_files
    if _loaded_files:
        return
    _loaded_files = True
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        entries = os.listdir(base)
    except OSError:
        return
    for fname in sorted(entries):
        if not (fname.startswith("lang_") and fname.endswith(".json")):
            continue
        code = fname[5:-5].lower()
        if not code or code == "en":
            continue
        path = os.path.join(base, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            log.warning("i18n failed loading %s: %s", fname, exc)
            continue
        if isinstance(data, dict) and "exact" in data and isinstance(data["exact"], dict):
            _PACKS[code] = _compile_v2_pack(data)
        elif isinstance(data, dict):
            _PACKS[code] = _compile_legacy_pack(data)
        log.info(
            "i18n loaded %s: %d exact, %d whole patterns, %d line patterns",
            code,
            len(_PACKS[code]["exact"]),
            len(_PACKS[code]["whole_pats"]),
            len(_PACKS[code]["line_pats"]),
        )


def available_languages() -> set[str]:
    _load_files()
    return {"en"} | set(_PACKS.keys())


def _apply_repl(repl: str, match: re.Match, lang: str, depth: int) -> str:
    def _sub(m: re.Match) -> str:
        is_tr = bool(m.group(1))
        gidx = int(m.group(2))
        val = match.group(gidx)
        if val is None:
            return ""
        if not is_tr or depth >= 4:
            return val
        return tr(val, lang, depth + 1) or val

    out = _REPL_TOKEN.sub(_sub, repl)
    return out.replace("{{", "{").replace("}}", "}")


def _apply_phrases_html_safe(text: str, phrases: list[tuple[str, str]]) -> str:
    if not phrases or not _HAS_LETTER_RE.search(text):
        return text
    for src, dst in phrases:
        if "<" in src and src in text:
            text = text.replace(src, dst)
    parts = _HTML_TAG_SPLIT.split(text)
    for i in range(0, len(parts), 2):
        seg = parts[i]
        if not seg or not _HAS_LETTER_RE.search(seg):
            continue
        for src, dst in phrases:
            if "<" not in src and src in seg:
                seg = seg.replace(src, dst)
        parts[i] = seg
    return "".join(parts)


def _match_patterns(text: str, pats: list[tuple[re.Pattern, str]], lang: str, depth: int) -> str | None:
    for rx, repl in pats:
        m = rx.fullmatch(text)
        if m is not None:
            try:
                return _apply_repl(repl, m, lang, depth)
            except Exception:
                return None
    return None


def _exact_lookup(text: str, exact: dict[str, str]) -> str | None:
    hit = exact.get(text)
    if hit is not None:
        return hit
    if "&" in text and _ENTITY_RE.search(text):
        unesc = html.unescape(text)
        hit = exact.get(unesc)
        if hit is not None:
            return html.escape(hit)
    return None


def _translate_line(line: str, pack: dict[str, Any], lang: str, depth: int) -> str:
    if not line or not _HAS_LETTER_RE.search(line):
        return line
    hit = _exact_lookup(line, pack["exact"])
    if hit is not None:
        return hit
    hit = _match_patterns(line, pack["line_pats"], lang, depth)
    if hit is not None:
        return hit
    stripped = line.strip()
    if stripped != line:
        lead = line[: len(line) - len(line.lstrip())]
        trail = line[len(line.rstrip()) :]
        hit = _exact_lookup(stripped, pack["exact"]) or _match_patterns(
            stripped, pack["line_pats"], lang, depth
        )
        if hit is not None:
            return f"{lead}{hit}{trail}"
    return _apply_phrases_html_safe(line, pack["phrases"])


def tr(text: str | None, lang: str = "hi", _depth: int = 0) -> str | None:
    """Translate text into `lang`. Returns original text if English or no pack loaded."""
    if not isinstance(text, str) or not text or lang == "en":
        return text
    _load_files()
    pack = _PACKS.get(lang)
    if pack is None or not _HAS_LETTER_RE.search(text):
        return text

    hit = _exact_lookup(text, pack["exact"])
    if hit is not None:
        return hit

    if "\n" in text:
        hit = _match_patterns(text, pack["whole_pats"], lang, _depth)
        if hit is not None:
            return hit
        lines = text.split("\n")
        out_lines = [_translate_line(ln, pack, lang, _depth) for ln in lines]
        return "\n".join(out_lines)

    hit = _match_patterns(text, pack["line_pats"], lang, _depth)
    if hit is not None:
        return hit
    hit = _match_patterns(text, pack["whole_pats"], lang, _depth)
    if hit is not None:
        return hit
    return _apply_phrases_html_safe(text, pack["phrases"])


def _tr_markup(markup: Any, lang: str) -> Any:
    if not isinstance(markup, InlineKeyboardMarkup) or lang == "en":
        return markup
    changed = False
    new_rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            txt = b.text
            if txt and _HAS_LETTER_RE.search(txt) and not _SKIP_BTN_RE.match(txt):
                nt = tr(txt, lang)
                if nt and nt != txt:
                    b = b.model_copy(update={"text": nt})
                    changed = True
            new_row.append(b)
        new_rows.append(new_row)
    if not changed:
        return markup
    return markup.model_copy(update={"inline_keyboard": new_rows})


def _resolve_lang(bot_id: int, method: TelegramMethod[Any]) -> str:
    chat_id_attr = getattr(method, "chat_id", None)
    ctx_chat, _ctx_type, ctx_user = CUR_CTX.get()
    cur_bot = bot_context.CURRENT_BOT_CTX.get()
    default_lang = (
        str(cur_bot.settings.get("default_lang") or "en")
        if (cur_bot is not None and cur_bot.bot_id == bot_id)
        else "en"
    )

    if isinstance(chat_id_attr, int):
        if chat_id_attr < 0:
            return CHAT_LANGS.get((bot_id, chat_id_attr), default_lang)
        u_lang = USER_LANGS.get((bot_id, chat_id_attr))
        if u_lang:
            return u_lang
        if ctx_chat is not None and ctx_chat < 0:
            return CHAT_LANGS.get((bot_id, ctx_chat), default_lang)
        return default_lang

    if ctx_chat is not None and ctx_chat < 0:
        return CHAT_LANGS.get((bot_id, ctx_chat), default_lang)
    if ctx_user is not None:
        return USER_LANGS.get((bot_id, ctx_user), default_lang)
    return default_lang


def _extract_update_context(update: Update) -> tuple[int | None, str | None, int | None]:
    for attr in (
        "message",
        "edited_message",
        "callback_query",
        "inline_query",
        "chosen_inline_result",
        "pre_checkout_query",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
    ):
        obj = getattr(update, attr, None)
        if obj is None:
            continue
        user = getattr(obj, "from_user", None)
        uid = user.id if user is not None else None
        chat = getattr(obj, "chat", None)
        if chat is None:
            msg = getattr(obj, "message", None)
            chat = getattr(msg, "chat", None) if msg is not None else None
        cid = chat.id if chat is not None else None
        ctype = getattr(chat, "type", None) if chat is not None else None
        return cid, ctype, uid
    return None, None, None


class I18nUserMiddleware(BaseMiddleware):
    """Records active (chat_id, chat_type, user_id) in ContextVar for outgoing localization."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            cid, ctype, uid = _extract_update_context(event)
            t1 = CUR_CTX.set((cid, ctype, uid))
            t2 = CUR.set(uid)
            try:
                return await handler(event, data)
            finally:
                CUR_CTX.reset(t1)
                CUR.reset(t2)
        return await handler(event, data)


class I18nRequestMiddleware(BaseRequestMiddleware):
    """Translates outgoing texts, captions, inline keyboards, callback alerts, and invoices per bot."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if _PACKS:
            lang = _resolve_lang(bot.id, method)
            if lang != "en" and lang in _PACKS:
                try:
                    updates: dict[str, Any] = {}
                    if isinstance(method, (SendMessage, EditMessageText)):
                        if method.text:
                            nt = tr(method.text, lang)
                            if nt and nt != method.text:
                                updates["text"] = nt
                        if method.reply_markup is not None:
                            nm = _tr_markup(method.reply_markup, lang)
                            if nm is not method.reply_markup:
                                updates["reply_markup"] = nm
                    elif isinstance(
                        method,
                        (SendPhoto, EditMessageCaption, SendAnimation, SendVideo, SendDocument, SendVoice, SendAudio),
                    ):
                        if method.caption:
                            nc = tr(method.caption, lang)
                            if nc and nc != method.caption:
                                updates["caption"] = nc
                        if method.reply_markup is not None:
                            nm = _tr_markup(method.reply_markup, lang)
                            if nm is not method.reply_markup:
                                updates["reply_markup"] = nm
                    elif isinstance(method, EditMessageReplyMarkup):
                        if method.reply_markup is not None:
                            nm = _tr_markup(method.reply_markup, lang)
                            if nm is not method.reply_markup:
                                updates["reply_markup"] = nm
                    elif isinstance(method, AnswerCallbackQuery):
                        if method.text:
                            nt = tr(method.text, lang)
                            if nt and nt != method.text:
                                updates["text"] = nt[:200]
                    elif isinstance(method, SendInvoice):
                        if method.title:
                            nt = tr(method.title, lang)
                            if nt and nt != method.title:
                                updates["title"] = nt[:32]
                        if method.description:
                            nd = tr(method.description, lang)
                            if nd and nd != method.description:
                                updates["description"] = nd[:255]
                        if method.prices:
                            new_prices = []
                            p_changed = False
                            for pr in method.prices:
                                nl = tr(pr.label, lang)
                                if nl and nl != pr.label:
                                    new_prices.append(LabeledPrice(label=nl, amount=pr.amount))
                                    p_changed = True
                                else:
                                    new_prices.append(pr)
                            if p_changed:
                                updates["prices"] = new_prices
                    if updates:
                        method = method.model_copy(update=updates)
                except Exception:
                    log.exception("i18n translation middleware error")
        return await make_request(bot, method)


async def load(pool: asyncpg.Pool) -> None:
    """Load saved per-bot user and group language preferences from Postgres."""
    _load_files()
    main_bid = bot_context.get_main_context().bot_id
    try:
        async with pool.acquire() as c:
            await c.execute(
                "CREATE TABLE IF NOT EXISTS ax_chat_lang ("
                "chat_id BIGINT PRIMARY KEY, "
                "lang TEXT NOT NULL DEFAULT 'en', "
                "bot_id BIGINT NOT NULL DEFAULT 0)"
            )
            u_rows = await c.fetch("SELECT bot_id, user_id, lang FROM ax_user_lang WHERE lang <> 'en'")
            c_rows = await c.fetch("SELECT bot_id, chat_id, lang FROM ax_chat_lang WHERE lang <> 'en'")
        USER_LANGS.clear()
        HI_USERS.clear()
        for r in u_rows:
            bid = r["bot_id"] or main_bid
            code = r["lang"]
            USER_LANGS[(bid, r["user_id"])] = code
            if code == "hi":
                HI_USERS.add((bid, r["user_id"]))
        CHAT_LANGS.clear()
        for r in c_rows:
            bid = r["bot_id"] or main_bid
            CHAT_LANGS[(bid, r["chat_id"])] = r["lang"]
        log.info("i18n loaded %d user prefs, %d group prefs", len(USER_LANGS), len(CHAT_LANGS))
    except Exception:
        log.exception("i18n load failed")


def set_user_lang(uid: int, code: str, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    key = (bid, uid)
    if code == "en":
        USER_LANGS.pop(key, None)
        HI_USERS.discard(key)
    else:
        USER_LANGS[key] = code
        if code == "hi":
            HI_USERS.add(key)
        else:
            HI_USERS.discard(key)


def set_chat_lang(chat_id: int, code: str, bot_id: int | None = None) -> None:
    bid = bot_context.resolve_bot_id(bot_id)
    key = (bid, chat_id)
    if code == "en":
        CHAT_LANGS.pop(key, None)
    else:
        CHAT_LANGS[key] = code


def set_lang(uid: int, code: str, bot_id: int | None = None) -> None:
    """Backward-compatible alias for set_user_lang()."""
    set_user_lang(uid, code, bot_id=bot_id)


def get_active_lang(
    chat_id: int | None = None,
    user_id: int | None = None,
    bot_id: int | None = None,
) -> str:
    bid = bot_context.resolve_bot_id(bot_id)
    if chat_id is not None and chat_id < 0:
        return CHAT_LANGS.get((bid, chat_id), "en")
    if user_id is not None:
        return USER_LANGS.get((bid, user_id), "en")
    return "en"


def register(dp, bot: Bot) -> None:
    global _registered
    _load_files()
    if _registered:
        return
    _registered = True
    dp.update.outer_middleware(I18nUserMiddleware())
    bot.session.middleware(I18nRequestMiddleware())

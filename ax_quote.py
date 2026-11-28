"""/q quotes one message, /q r also shows the message it replied to, /q N stacks N messages (aiogram 3.x).

Multi-bot safe: every cache is keyed by bot id and bounded in size. Heavy work (Pillow, ffmpeg)
runs in worker threads; only QUOTE_RENDER_SLOTS renders run at once for the whole process, so
RAM stays low on small hosts.
"""
import asyncio
import ctypes
import gc
import html as _html
import io
import logging
import os
import subprocess
import tempfile
import time
from collections import OrderedDict

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from PIL import Image

import ax_qextra as qx
import quote_cache as qc
import quote_emoji as qe
import quote_pil as qp
from ax_common import cmd_args, run_heavy

log = logging.getLogger(__name__)
router = Router(name="ax_quote")

H = ParseMode.HTML

# ---- Limits (override with environment variables) -------------------------------------------
RENDER_SLOTS = max(1, int(os.environ.get("QUOTE_RENDER_SLOTS", "1")))  # renders at once, whole process
MAX_INFLIGHT = max(1, int(os.environ.get("QUOTE_MAX_INFLIGHT", "6")))  # /q jobs waiting or running
MAX_DOWNLOAD = 8 * 1024 * 1024  # never download files bigger than this for a quote
PHOTO_MAX_SIDE = 1000  # photos are drawn at most ~700 px wide, so bigger inputs are wasted RAM
AVATAR_MIN_SIDE = 200  # profile photo drawn at ~100 px; smallest size >= this is plenty

# ---- Per-bot caches, all bounded ------------------------------------------------------------
_ROLE_TTL = 600
_AVATAR_TTL = 300
_ROLE_MAX = 3000
_AVATAR_MAX = 200
_CUSTOM_EMOJI_MAX = 300
_MEDIA_BUDGET = 16 * 1024 * 1024  # bytes of downloaded stickers/thumbnails kept in RAM

_ROLE_CACHE: "OrderedDict[tuple[int, int, int], tuple[float, tuple[str, str | None] | None]]" = OrderedDict()
_AVATAR_CACHE: "OrderedDict[tuple[int, int], tuple[float, bytes]]" = OrderedDict()
_CUSTOM_EMOJI_CACHE: "OrderedDict[tuple[int, str], bytes]" = OrderedDict()
_MEDIA_CACHE: "OrderedDict[tuple[int, str], bytes]" = OrderedDict()
_media_bytes = 0

_render_sem: asyncio.Semaphore | None = None
_inflight = 0
_trim_fn = None


def _cache_put(store: OrderedDict, key, val, limit: int) -> None:
    store[key] = val
    store.move_to_end(key)
    while len(store) > limit:
        store.popitem(last=False)


def _media_put(key: tuple[int, str], data: bytes) -> None:
    global _media_bytes
    if len(data) > _MEDIA_BUDGET // 4:
        return
    old = _MEDIA_CACHE.pop(key, None)
    if old is not None:
        _media_bytes -= len(old)
    _MEDIA_CACHE[key] = data
    _media_bytes += len(data)
    while _media_bytes > _MEDIA_BUDGET and _MEDIA_CACHE:
        _k, v = _MEDIA_CACHE.popitem(last=False)
        _media_bytes -= len(v)


def _get_sem() -> asyncio.Semaphore:
    global _render_sem
    if _render_sem is None:
        _render_sem = asyncio.Semaphore(RENDER_SLOTS)
    return _render_sem


def _release_memory() -> None:
    """Hand freed memory back to the OS (glibc keeps big freed blocks otherwise)."""
    global _trim_fn
    gc.collect()
    try:
        if _trim_fn is None:
            _trim_fn = ctypes.CDLL("libc.so.6").malloc_trim
        _trim_fn(0)
    except Exception:
        pass


# ---- Downloads ------------------------------------------------------------------------------

async def _dl_file_bytes(bot: Bot, file_id: str | None, max_size: int = MAX_DOWNLOAD) -> bytes | None:
    if not file_id:
        return None
    try:
        f = await bot.get_file(file_id, request_timeout=15)
        if not f.file_path:
            return None
        if f.file_size and f.file_size > max_size:
            return None
        buf = io.BytesIO()
        await bot.download_file(f.file_path, destination=buf, timeout=30)
        return buf.getvalue()
    except Exception as e:
        log.warning("quote file dl failed: %r", str(e)[:100])
        return None


async def _dl_cached(bot: Bot, bot_id: int, file_id: str | None, unique_id: str | None, max_size: int = MAX_DOWNLOAD) -> bytes | None:
    """Download once, then serve from a small bounded RAM cache (same sticker quoted again = no download)."""
    if not file_id:
        return None
    key = (bot_id, unique_id or file_id)
    hit = _MEDIA_CACHE.get(key)
    if hit is not None:
        _MEDIA_CACHE.move_to_end(key)
        return hit
    data = await _dl_file_bytes(bot, file_id, max_size)
    if data:
        _media_put(key, data)
    return data


def _shrink_image(raw: bytes, max_side: int) -> bytes:
    """Make an image no bigger than max_side. Returns the original bytes when it is already small."""
    try:
        im = Image.open(io.BytesIO(raw))
        w, h = im.size
        if max(w, h) <= max_side:
            return raw
        if im.format == "JPEG":
            im.draft("RGB", (max_side, max_side))
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        out = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            im.convert("RGBA").save(out, format="PNG")
        else:
            im.convert("RGB").save(out, format="JPEG", quality=92)
        im.close()
        return out.getvalue()
    except Exception:
        return raw


async def _dl_image(bot: Bot, bot_id: int, file_id: str | None, unique_id: str | None, max_side: int = PHOTO_MAX_SIDE) -> bytes | None:
    """Photo bytes already shrunk to a useful size; the shrunk copy is what gets cached."""
    if not file_id:
        return None
    key = (bot_id, "i%d:%s" % (max_side, unique_id or file_id))
    hit = _MEDIA_CACHE.get(key)
    if hit is not None:
        _MEDIA_CACHE.move_to_end(key)
        return hit
    data = await _dl_file_bytes(bot, file_id)
    if data and len(data) > 120_000:
        data = await asyncio.to_thread(_shrink_image, data, max_side)
    if data:
        _media_put(key, data)
    return data


def _extract_video_frame_sync(video_bytes: bytes) -> bytes | None:
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            tf.write(video_bytes)
            tmp_path = tf.name
        res = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", tmp_path, "-frames:v", "1",
                "-vf", "scale='min(%d,iw)':-2" % PHOTO_MAX_SIDE,
                "-f", "image2pipe", "-c:v", "png", "pipe:1",
            ],
            capture_output=True,
            timeout=12,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout
    except Exception as e:
        log.warning("ffmpeg frame extract failed: %r", str(e)[:100])
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return None


async def _video_frame(bot: Bot, bot_id: int, file_id: str, unique_id: str | None) -> bytes | None:
    raw = await _dl_cached(bot, bot_id, file_id, unique_id, 6 * 1024 * 1024)
    if not raw:
        return None
    return await asyncio.to_thread(_extract_video_frame_sync, raw)


def _pick_photo(sizes):
    """Smallest profile photo that is still big enough for the ~100 px avatar."""
    for s in sizes:
        if (getattr(s, "width", 0) or 0) >= AVATAR_MIN_SIDE or (getattr(s, "height", 0) or 0) >= AVATAR_MIN_SIDE:
            return s
    return sizes[-1]


async def _get_avatar(bot: Bot, m: Message, bot_id: int) -> bytes | None:
    u, c = getattr(m, "from_user", None), getattr(m, "sender_chat", None)
    key_id = u.id if u else (c.id if c else 0)
    if not key_id:
        return None
    key = (bot_id, key_id)
    now = time.time()
    hit = _AVATAR_CACHE.get(key)
    if hit and now - hit[0] < _AVATAR_TTL:
        _AVATAR_CACHE.move_to_end(key)
        return hit[1] or None
    av = None
    if u:
        try:
            ph = await bot.get_user_profile_photos(u.id, limit=1, request_timeout=15)
            if ph.total_count and ph.photos:
                sz = _pick_photo(ph.photos[0])
                av = await _dl_file_bytes(bot, sz.file_id, 2 * 1024 * 1024)
        except Exception as e:
            log.warning("quote photo failed: %r", str(e)[:100])
    elif c:
        try:
            chat_obj = await bot.get_chat(c.id, request_timeout=15)
            ph = getattr(chat_obj, "photo", None)
            fid = (getattr(ph, "small_file_id", None) or getattr(ph, "big_file_id", None)) if ph else None
            av = await _dl_file_bytes(bot, fid, 2 * 1024 * 1024)
        except Exception:
            pass
    if av and len(av) > 120_000:
        av = await asyncio.to_thread(_shrink_image, av, 256)
    _cache_put(_AVATAR_CACHE, key, (now, av or b""), _AVATAR_MAX)  # remember "no photo" too
    return av


async def _roles(bot: Bot, chat_id: int, msgs: list[Message], bot_id: int | None = None) -> dict:
    bot_id = bot_id or bot.id
    out, now = {}, time.time()

    async def one(u, key):
        val = None
        try:
            cm = await bot.get_chat_member(chat_id, u.id, request_timeout=15)
            if cm.status == ChatMemberStatus.CREATOR:
                val = ("owner", getattr(cm, "custom_title", None))
            elif cm.status == ChatMemberStatus.ADMINISTRATOR:
                val = ("admin", getattr(cm, "custom_title", None))
        except Exception as e:
            log.warning("quote role failed: %r", str(e)[:100])
        _cache_put(_ROLE_CACHE, key, (now, val), _ROLE_MAX)
        return u.id, val

    todo = {}
    for m in msgs:
        u = getattr(m, "from_user", None)
        if u is None or m.chat.type not in ("group", "supergroup"):
            continue
        key = (bot_id, chat_id, u.id)
        hit = _ROLE_CACHE.get(key)
        if hit and now - hit[0] < _ROLE_TTL:
            _ROLE_CACHE.move_to_end(key)
            if hit[1]:
                out[u.id] = hit[1]
        else:
            todo[u.id] = (u, key)
    if todo:
        for uid, val in await asyncio.gather(*(one(u, k) for u, k in todo.values())):
            if val:
                out[uid] = val
    return out


async def _fetch_custom_emojis(bot: Bot, custom_ids: list[str], bot_id: int) -> dict[str, bytes]:
    out, missing = {}, []
    for cid in set(custom_ids):
        key = (bot_id, cid)
        if key in _CUSTOM_EMOJI_CACHE:
            _CUSTOM_EMOJI_CACHE.move_to_end(key)
            out[cid] = _CUSTOM_EMOJI_CACHE[key]
        else:
            missing.append(cid)
    if not missing:
        return out
    try:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=missing[:50], request_timeout=15)

        async def one(st):
            cid = str(st.custom_emoji_id)
            thumb = getattr(st, "thumbnail", None) or getattr(st, "thumb", None)
            fid = thumb.file_id if thumb else (st.file_id if not (st.is_animated or st.is_video) else None)
            return cid, (await _dl_file_bytes(bot, fid, 1024 * 1024) if fid else None)

        for cid, b in await asyncio.gather(*(one(st) for st in stickers)):
            if b:
                _cache_put(_CUSTOM_EMOJI_CACHE, (bot_id, cid), b, _CUSTOM_EMOJI_MAX)
                out[cid] = b
    except Exception as e:
        log.warning("custom emoji fetch failed: %r", str(e)[:100])
    return out


# ---- Message -> dict for the renderer --------------------------------------------------------

def _sender_info(m: Message) -> tuple[str, int, int | None, str | None, str | None]:
    u = getattr(m, "from_user", None)
    if u:
        return (
            u.full_name or u.first_name or "User",
            u.id,
            getattr(u, "accent_color_id", None),
            getattr(u, "emoji_status_custom_emoji_id", None),
            getattr(u, "background_custom_emoji_id", None),
        )
    c = getattr(m, "sender_chat", None)
    if c:
        return (
            getattr(c, "title", None) or "Channel",
            c.id,
            getattr(c, "accent_color_id", None),
            getattr(c, "emoji_status_custom_emoji_id", None),
            getattr(c, "background_custom_emoji_id", None),
        )
    return "Someone", 0, None, None, None


def _forward_label(m: Message) -> str | None:
    origin = getattr(m, "forward_origin", None)
    if origin:
        su = getattr(origin, "sender_user", None)
        if su:
            return f"Forwarded from {su.full_name or su.first_name}"
        sc = getattr(origin, "sender_chat", None) or getattr(origin, "chat", None)
        if sc and getattr(sc, "title", None):
            return f"Forwarded from {sc.title}"
        sname = getattr(origin, "sender_user_name", None)
        if sname:
            return f"Forwarded from {sname}"
        return "Forwarded message"
    return None


def _entity_to_dict(e) -> dict:
    if hasattr(e, "model_dump"):
        return e.model_dump(mode="json", exclude_none=True)
    if hasattr(e, "to_dict"):
        return e.to_dict()
    return dict(e)


def _thumb_of(obj):
    return getattr(obj, "thumbnail", None) or getattr(obj, "thumb", None)


async def _extract_media(bot: Bot, m: Message, bot_id: int) -> dict:
    res = {
        "media": None,
        "media_type": None,
        "media_duration": None,
        "media_spoiler": bool(getattr(m, "has_media_spoiler", False)),
        "voice": None,
        "document": None,
        "audio": None,
    }
    if getattr(m, "photo", None):
        p = m.photo[-1]
        res["media"] = await _dl_image(bot, bot_id, p.file_id, p.file_unique_id)
        res["media_type"] = "photo"
    elif getattr(m, "sticker", None):
        st = m.sticker
        thumb = _thumb_of(st)
        if st.is_animated or st.is_video:
            if thumb:
                res["media"] = await _dl_cached(bot, bot_id, thumb.file_id, thumb.file_unique_id, 1024 * 1024)
            elif st.is_video:
                res["media"] = await _video_frame(bot, bot_id, st.file_id, st.file_unique_id)
        else:
            res["media"] = await _dl_cached(bot, bot_id, st.file_id, st.file_unique_id, 2 * 1024 * 1024)
        res["media_type"] = "sticker"
    elif getattr(m, "video_note", None):
        vn = m.video_note
        thumb = _thumb_of(vn)
        if thumb:
            res["media"] = await _dl_cached(bot, bot_id, thumb.file_id, thumb.file_unique_id, 1024 * 1024)
        res["media_type"] = "video_note"
    elif getattr(m, "animation", None):
        anim = m.animation
        thumb = _thumb_of(anim)
        if thumb:
            res["media"] = await _dl_cached(bot, bot_id, thumb.file_id, thumb.file_unique_id, 1024 * 1024)
        else:
            res["media"] = await _video_frame(bot, bot_id, anim.file_id, anim.file_unique_id)
        res["media_type"] = "gif"
    elif getattr(m, "video", None):
        vid = m.video
        thumb = _thumb_of(vid)
        if thumb:
            res["media"] = await _dl_cached(bot, bot_id, thumb.file_id, thumb.file_unique_id, 1024 * 1024)
        else:
            res["media"] = await _video_frame(bot, bot_id, vid.file_id, vid.file_unique_id)
        res["media_type"], res["media_duration"] = "video", getattr(vid, "duration", None)
    elif getattr(m, "voice", None):
        res["voice"] = {"duration": getattr(m.voice, "duration", 0), "waveform": None}
    elif getattr(m, "audio", None):
        a = m.audio
        thumb = _thumb_of(a)
        res["audio"] = {
            "title": getattr(a, "title", None) or getattr(a, "file_name", None) or "Audio",
            "performer": getattr(a, "performer", None),
            "duration": getattr(a, "duration", None),
            "thumb": (await _dl_cached(bot, bot_id, thumb.file_id, thumb.file_unique_id, 1024 * 1024) if thumb else None),
        }
    elif getattr(m, "document", None):
        d = m.document
        if (getattr(d, "mime_type", "") or "").startswith("image/"):
            res["media"] = await _dl_image(bot, bot_id, d.file_id, d.file_unique_id)
            res["media_type"] = "photo"
        else:
            res["document"] = {
                "file_name": getattr(d, "file_name", None) or "File",
                "file_size": getattr(d, "file_size", None),
            }
    return res


async def _reply_dict(bot: Bot, m: Message, bot_id: int, include_reply: bool, multi: bool) -> dict | None:
    r, quote_part = getattr(m, "reply_to_message", None), getattr(m, "quote", None)
    if r is None or not (include_reply or quote_part is not None or multi):
        return None
    r_name, r_uid, r_acc_id, _, r_bg_em = _sender_info(r)
    has_part = bool(quote_part and getattr(quote_part, "text", None))
    r_text = quote_part.text.strip() if has_part else (r.text or r.caption or "").strip()
    r_ents = (getattr(quote_part, "entities", None) or []) if has_part else ((r.entities if r.text else r.caption_entities) or [])
    r_thumb = None
    if getattr(r, "photo", None):
        r_thumb = await _dl_cached(bot, bot_id, r.photo[0].file_id, r.photo[0].file_unique_id, 1024 * 1024)
        if not r_text:
            r_text = "Photo"
    elif getattr(r, "sticker", None):
        th = _thumb_of(r.sticker)
        if th:
            r_thumb = await _dl_cached(bot, bot_id, th.file_id, th.file_unique_id, 1024 * 1024)
        if not r_text:
            r_text = f"{r.sticker.emoji or ''} Sticker".strip()
    elif getattr(r, "video", None):
        th = _thumb_of(r.video)
        if th:
            r_thumb = await _dl_cached(bot, bot_id, th.file_id, th.file_unique_id, 1024 * 1024)
        if not r_text:
            r_text = "Video"
    elif getattr(r, "voice", None) and not r_text:
        r_text = "Voice message"
    elif getattr(r, "document", None) and not r_text:
        r_text = getattr(r.document, "file_name", None) or "Document"
    if not (r_text or r_thumb):
        return None
    return {
        "name": r_name,
        "text": r_text,
        "user_id": r_uid,
        "accent_color_id": r_acc_id,
        "background_emoji_id": str(r_bg_em) if r_bg_em else None,
        "entities": [_entity_to_dict(e) for e in r_ents],
        "thumb": r_thumb,
    }


async def _build_msg_dicts(
    bot: Bot,
    msgs: list[Message],
    roles: dict,
    include_reply: bool = False,
    bot_id: int | None = None,
) -> list[dict]:
    bot_id = bot_id or bot.id
    c_ids: list[str] = []
    texts: list[str] = []

    for m in msgs:
        name_preview, _, _, em_status, _ = _sender_info(m)
        texts.append(name_preview)
        texts.append(m.text or m.caption or "")
        for e in (m.entities if m.text else m.caption_entities) or []:
            if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None):
                c_ids.append(str(e.custom_emoji_id))
        if em_status:
            c_ids.append(str(em_status))
        r = getattr(m, "reply_to_message", None)
        if r is not None:
            r_name_preview, _, _, _, r_bg_em = _sender_info(r)
            texts.append(r_name_preview)
            texts.append(r.text or r.caption or "")
            if getattr(r, "sticker", None):
                texts.append(r.sticker.emoji or "")
            if r_bg_em:
                c_ids.append(str(r_bg_em))
            for e in (r.entities if r.text else r.caption_entities) or []:
                if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None):
                    c_ids.append(str(e.custom_emoji_id))

    # custom emojis and standard emoji pictures download in parallel, outside the render thread
    custom_emojis, _ = await asyncio.gather(
        _fetch_custom_emojis(bot, c_ids, bot_id) if c_ids else asyncio.sleep(0, result={}),
        qe.prefetch(texts),
    )

    multi = len(msgs) > 1

    async def one(m: Message) -> dict | None:
        text = m.text or m.caption or ""
        ents = (m.entities if m.text else m.caption_entities) or []
        name, uid, acc_id, em_status, _ = _sender_info(m)
        avatar, media_info, reply_dict = await asyncio.gather(
            _get_avatar(bot, m, bot_id),
            _extract_media(bot, m, bot_id),
            _reply_dict(bot, m, bot_id, include_reply, multi),
        )
        if not bool(text or media_info["media"] or media_info["voice"] or media_info["document"] or media_info["audio"]):
            return None
        via_u = getattr(m, "via_bot", None)
        return {
            "name": name,
            "user_id": uid,
            "accent_color_id": acc_id,
            "emoji_status": str(em_status) if em_status else None,
            "text": text,
            "entities": [_entity_to_dict(e) for e in ents],
            "custom_emojis": custom_emojis,
            "avatar": avatar,
            "tag": roles.get(uid),
            "reply": reply_dict,
            "via_bot": getattr(via_u, "username", None) if via_u else None,
            "forward_label": _forward_label(m),
            **media_info,
        }

    out = [d for d in await asyncio.gather(*(one(m) for m in msgs)) if d]

    # strings built above (sticker labels, file names, forward labels ...) may hold emojis too
    extra: list[str] = []
    for d in out:
        extra.append(d.get("forward_label") or "")
        extra.append(d.get("via_bot") or "")
        for k in ("audio", "document"):
            v = d.get(k)
            if isinstance(v, dict):
                extra.extend(str(x) for x in v.values() if isinstance(x, str))
        rp = d.get("reply")
        if rp:
            extra.append(rp.get("text") or "")
    await qe.prefetch(extra)
    return out


# ---- Rendering -----------------------------------------------------------------------------

def _encode_webp(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="WEBP", lossless=True, quality=95, method=2)
    if out.tell() > 500_000:
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=85)
    return out.getvalue()


def _render_webp_bytes(msg_dicts: list[dict], color: str | None) -> bytes:
    img = None
    try:
        img = qp.render_messages(msg_dicts, color)
        return _encode_webp(img)
    finally:
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
        img = None
        _release_memory()


@router.message(Command("q"))
async def quote_cmd(message: Message, bot: Bot) -> None:
    global _inflight
    bot_id = bot.id
    p = qx.parse_args(cmd_args(message))
    rp, multi = message.reply_to_message, None
    chat_id = message.chat.id

    if p["n"] > 1:
        if rp:
            multi = qc.span(chat_id, rp.message_id, p["n"], message.message_id, bot_id)
            if len(multi) < p["n"]:
                first = multi[0].message_id if multi else message.message_id
                multi = qc.last(chat_id, first, p["n"] - len(multi), bot_id) + multi
        else:
            multi = qc.last(chat_id, message.message_id, p["n"], bot_id)
        if len(multi) < 2:
            await message.reply(
                "\N{CHERRY BLOSSOM} <b>Not enough messages yet, %s</b>\n<blockquote>I only remember messages sent since my last restart. Try again after a few more.</blockquote>"
                % qx._name(message.from_user),
                parse_mode=H,
            )
            return
        rp = rp or multi[0]

    if not rp:
        await message.reply(
            "\N{SPEECH BALLOON} <b>Reply to a message</b>\n<blockquote>Use /q on any message, or /q r to include the message it replied to \N{SPARKLES}</blockquote>",
            parse_mode=H,
        )
        return

    rp = qc.get(chat_id, rp.message_id, bot_id) or rp
    if p["bad"]:
        await message.reply(
            "\N{ARTIST PALETTE} <b>Didn't know</b> <code>%s</code>\n<blockquote>Try /qcolors to see colour names.%s</blockquote>"
            % (_html.escape(p["bad"][0]), qx.hint(p["bad"][0])),
            parse_mode=H,
        )

    if _inflight >= MAX_INFLIGHT:
        await message.reply(
            "\N{HOURGLASS WITH FLOWING SAND} <b>Busy right now</b>\n<blockquote>Too many quotes are being made at once. Try again in a few seconds.</blockquote>",
            parse_mode=H,
        )
        return

    msgs = multi or [rp]
    _inflight += 1
    msg_dicts: list[dict] = []
    try:
        roles = await _roles(bot, chat_id, msgs, bot_id)
        msg_dicts = await _build_msg_dicts(bot, msgs, roles, include_reply=bool(p["r"]), bot_id=bot_id)
        if not msg_dicts:
            await message.reply(
                "\N{CHERRY BLOSSOM} <b>No content found</b>\n<blockquote>I couldn't find any text or media to quote \N{CHERRY BLOSSOM}</blockquote>",
                parse_mode=H,
            )
            return
        async with _get_sem():  # one heavy render at a time for the whole process
            webp_bytes = await run_heavy(_render_webp_bytes, msg_dicts, p["color"])
        msg_dicts.clear()
        await message.reply_sticker(
            sticker=BufferedInputFile(webp_bytes, filename="quote.webp"),
            request_timeout=30,
        )
    except Exception as e:
        log.exception("quote_cmd failed")
        await message.reply(
            "\N{WARNING SIGN}\N{VARIATION SELECTOR-16} <b>Couldn't make the sticker</b>\n<blockquote>%s</blockquote>"
            % _html.escape(str(e)[:200]),
            parse_mode=H,
        )
    finally:
        _inflight -= 1
        msg_dicts.clear()


async def on_shutdown() -> None:
    await qe.close_clients()


def register(dp) -> None:
    qc.register(dp)
    if router.parent_router is None:
        dp.include_router(router)

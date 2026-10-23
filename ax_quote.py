"""/q quotes one message, /q r also shows the message it replied to, /q N stacks N messages."""
import asyncio, html as _html, io, os, subprocess, tempfile, time
from telegram.constants import ParseMode
from telegram.ext import CommandHandler
import ax_qextra as qx
import quote_cache as qc
import quote_pil as qp

H = ParseMode.HTML
_ROLE_CACHE, _AVATAR_CACHE, _CUSTOM_EMOJI_CACHE = {}, {}, {}

async def _dl_file_bytes(bot, file_id: str) -> bytes | None:
    if not file_id: return None
    try:
        f = await bot.get_file(file_id)
        buf = io.BytesIO()
        await f.download_to_memory(out=buf)
        return buf.getvalue()
    except Exception as e:
        print("quote file dl failed:", repr(e)[:100])
        return None

def _extract_video_frame_sync(video_bytes: bytes) -> bytes | None:
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            tf.write(video_bytes)
            tmp_path = tf.name
        res = subprocess.run(["ffmpeg", "-y", "-i", tmp_path, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"], capture_output=True, timeout=12, check=False)
        if res.returncode == 0 and res.stdout: return res.stdout
    except Exception as e: print("ffmpeg frame extract failed:", repr(e)[:100])
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass
    return None

async def _get_avatar(bot, m) -> bytes | None:
    u, c = getattr(m, "from_user", None), getattr(m, "sender_chat", None)
    key = u.id if u else (c.id if c else 0)
    if not key: return None
    now = time.time()
    hit = _AVATAR_CACHE.get(key)
    if hit and now - hit[0] < 300: return hit[1]
    av = None
    if u:
        try:
            ph = await bot.get_user_profile_photos(u.id, limit=1)
            if ph.total_count: av = await _dl_file_bytes(bot, ph.photos[0][-1].file_id)
        except Exception as e: print("quote photo failed:", repr(e)[:100])
    elif c:
        try:
            chat_obj = await bot.get_chat(c.id)
            if getattr(chat_obj, "photo", None) and chat_obj.photo.big_file_id:
                av = await _dl_file_bytes(bot, chat_obj.photo.big_file_id)
        except Exception: pass
    if av: _AVATAR_CACHE[key] = (now, av)
    return av

async def _roles(bot, chat_id: int, msgs: list) -> dict:
    out, now = {}, time.time()
    for m in msgs:
        u = getattr(m, "from_user", None)
        if u is None or m.chat.type not in ("group", "supergroup"): continue
        key = (chat_id, u.id)
        hit = _ROLE_CACHE.get(key)
        if hit and now - hit[0] < 600: val = hit[1]
        else:
            val = None
            try:
                cm = await bot.get_chat_member(chat_id, u.id)
                if cm.status == "creator": val = ("owner", getattr(cm, "custom_title", None))
                elif cm.status == "administrator": val = ("admin", getattr(cm, "custom_title", None))
            except Exception as e: print("quote role failed:", repr(e)[:100])
            _ROLE_CACHE[key] = (now, val)
        if val: out[u.id] = val
    return out

async def _fetch_custom_emojis(bot, custom_ids: list[str]) -> dict[str, bytes]:
    out, missing = {}, []
    for cid in set(custom_ids):
        if cid in _CUSTOM_EMOJI_CACHE: out[cid] = _CUSTOM_EMOJI_CACHE[cid]
        else: missing.append(cid)
    if not missing: return out
    try:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=missing[:50])
        for st in stickers:
            cid = str(st.custom_emoji_id)
            thumb = getattr(st, "thumbnail", None) or getattr(st, "thumb", None)
            fid = thumb.file_id if thumb else (st.file_id if not (st.is_animated or st.is_video) else None)
            if fid:
                b = await _dl_file_bytes(bot, fid)
                if b: _CUSTOM_EMOJI_CACHE[cid] = out[cid] = b
    except Exception as e: print("custom emoji fetch failed:", repr(e)[:100])
    return out

def _sender_info(m) -> tuple[str, int, int | None, str | None, str | None]:
    u = getattr(m, "from_user", None)
    if u: return (u.full_name or u.first_name or "User", u.id, getattr(u, "accent_color_id", None), getattr(u, "emoji_status_custom_emoji_id", None), getattr(u, "background_custom_emoji_id", None))
    c = getattr(m, "sender_chat", None)
    if c: return (getattr(c, "title", None) or "Channel", c.id, getattr(c, "accent_color_id", None), getattr(c, "emoji_status_custom_emoji_id", None), getattr(c, "background_custom_emoji_id", None))
    return "Someone", 0, None, None, None

def _forward_label(m) -> str | None:
    origin = getattr(m, "forward_origin", None)
    if origin:
        su = getattr(origin, "sender_user", None)
        if su: return f"Forwarded from {su.full_name or su.first_name}"
        sc = getattr(origin, "sender_chat", None) or getattr(origin, "chat", None)
        if sc and getattr(sc, "title", None): return f"Forwarded from {sc.title}"
        sname = getattr(origin, "sender_user_name", None)
        if sname: return f"Forwarded from {sname}"
        return "Forwarded message"
    if getattr(m, "forward_from", None): return f"Forwarded from {m.forward_from.full_name}"
    if getattr(m, "forward_from_chat", None): return f"Forwarded from {m.forward_from_chat.title}"
    if getattr(m, "forward_sender_name", None): return f"Forwarded from {m.forward_sender_name}"
    return None

async def _extract_media(bot, m) -> dict:
    res = {"media": None, "media_type": None, "media_duration": None, "media_spoiler": bool(getattr(m, "has_media_spoiler", False)), "voice": None, "document": None, "audio": None}
    if getattr(m, "photo", None):
        res["media"] = await _dl_file_bytes(bot, m.photo[-1].file_id); res["media_type"] = "photo"
    elif getattr(m, "sticker", None):
        st = m.sticker
        thumb = getattr(st, "thumbnail", None) or getattr(st, "thumb", None)
        if st.is_animated or st.is_video:
            if thumb: res["media"] = await _dl_file_bytes(bot, thumb.file_id)
            elif st.is_video:
                raw_v = await _dl_file_bytes(bot, st.file_id)
                if raw_v: res["media"] = await asyncio.to_thread(_extract_video_frame_sync, raw_v)
        else: res["media"] = await _dl_file_bytes(bot, st.file_id)
        res["media_type"] = "sticker"
    elif getattr(m, "video_note", None):
        thumb = getattr(m.video_note, "thumbnail", None) or getattr(m.video_note, "thumb", None)
        if thumb: res["media"] = await _dl_file_bytes(bot, thumb.file_id)
        res["media_type"] = "video_note"
    elif getattr(m, "animation", None):
        anim = m.animation
        thumb = getattr(anim, "thumbnail", None) or getattr(anim, "thumb", None)
        if thumb: res["media"] = await _dl_file_bytes(bot, thumb.file_id)
        else:
            raw_v = await _dl_file_bytes(bot, anim.file_id)
            if raw_v: res["media"] = await asyncio.to_thread(_extract_video_frame_sync, raw_v)
        res["media_type"] = "gif"
    elif getattr(m, "video", None):
        vid = m.video
        thumb = getattr(vid, "thumbnail", None) or getattr(vid, "thumb", None)
        if thumb: res["media"] = await _dl_file_bytes(bot, thumb.file_id)
        else:
            raw_v = await _dl_file_bytes(bot, vid.file_id)
            if raw_v: res["media"] = await asyncio.to_thread(_extract_video_frame_sync, raw_v)
        res["media_type"], res["media_duration"] = "video", getattr(vid, "duration", None)
    elif getattr(m, "voice", None):
        res["voice"] = {"duration": getattr(m.voice, "duration", 0), "waveform": None}
    elif getattr(m, "audio", None):
        a = m.audio
        thumb = getattr(a, "thumbnail", None) or getattr(a, "thumb", None)
        res["audio"] = {"title": getattr(a, "title", None) or getattr(a, "file_name", None) or "Audio", "performer": getattr(a, "performer", None), "duration": getattr(a, "duration", None), "thumb": (await _dl_file_bytes(bot, thumb.file_id) if thumb else None)}
    elif getattr(m, "document", None):
        d = m.document
        if (getattr(d, "mime_type", "") or "").startswith("image/"):
            res["media"] = await _dl_file_bytes(bot, d.file_id); res["media_type"] = "photo"
        else: res["document"] = {"file_name": getattr(d, "file_name", None) or "File", "file_size": getattr(d, "file_size", None)}
    return res

async def _build_msg_dicts(bot, msgs: list, roles: dict, include_reply: bool = False) -> list[dict]:
    c_ids = []
    for m in msgs:
        for e in ((m.entities if m.text else m.caption_entities) or []):
            if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None): c_ids.append(str(e.custom_emoji_id))
        _, _, _, em_status, _ = _sender_info(m)
        if em_status: c_ids.append(str(em_status))
        r = getattr(m, "reply_to_message", None)
        if r is not None:
            _, _, _, _, r_bg_em = _sender_info(r)
            if r_bg_em: c_ids.append(str(r_bg_em))
            for e in ((r.entities if r.text else r.caption_entities) or []):
                if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None): c_ids.append(str(e.custom_emoji_id))
    custom_emojis = await _fetch_custom_emojis(bot, c_ids) if c_ids else {}
    out = []
    for m in msgs:
        text = m.text or m.caption or ""
        ents = (m.entities if m.text else m.caption_entities) or []
        name, uid, acc_id, em_status, _ = _sender_info(m)
        avatar = await _get_avatar(bot, m)
        media_info = await _extract_media(bot, m)
        if not bool(text or media_info["media"] or media_info["voice"] or media_info["document"] or media_info["audio"]): continue
        reply_dict = None
        r, quote_part = getattr(m, "reply_to_message", None), getattr(m, "quote", None)
        if r is not None and (include_reply or quote_part is not None or len(msgs) > 1):
            r_name, r_uid, r_acc_id, _, r_bg_em = _sender_info(r)
            r_text = quote_part.text.strip() if (quote_part and getattr(quote_part, "text", None)) else (r.text or r.caption or "").strip()
            r_ents = (getattr(quote_part, "entities", None) or []) if (quote_part and getattr(quote_part, "text", None)) else ((r.entities if r.text else r.caption_entities) or [])
            r_thumb = None
            if getattr(r, "photo", None):
                r_thumb = await _dl_file_bytes(bot, r.photo[0].file_id)
                if not r_text: r_text = "Photo"
            elif getattr(r, "sticker", None):
                th = getattr(r.sticker, "thumbnail", None) or getattr(r.sticker, "thumb", None)
                if th: r_thumb = await _dl_file_bytes(bot, th.file_id)
                if not r_text: r_text = f"{r.sticker.emoji or ''} Sticker".strip()
            elif getattr(r, "video", None):
                th = getattr(r.video, "thumbnail", None) or getattr(r.video, "thumb", None)
                if th: r_thumb = await _dl_file_bytes(bot, th.file_id)
                if not r_text: r_text = "Video"
            elif getattr(r, "voice", None) and not r_text: r_text = "Voice message"
            elif getattr(r, "document", None) and not r_text: r_text = getattr(r.document, "file_name", None) or "Document"
            if r_text or r_thumb:
                reply_dict = {"name": r_name, "text": r_text, "user_id": r_uid, "accent_color_id": r_acc_id, "background_emoji_id": str(r_bg_em) if r_bg_em else None, "entities": [e.to_dict() for e in r_ents], "thumb": r_thumb}
        via_u = getattr(m, "via_bot", None)
        out.append({"name": name, "user_id": uid, "accent_color_id": acc_id, "emoji_status": str(em_status) if em_status else None, "text": text, "entities": [e.to_dict() for e in ents], "custom_emojis": custom_emojis, "avatar": avatar, "tag": roles.get(uid), "reply": reply_dict, "via_bot": getattr(via_u, "username", None) if via_u else None, "forward_label": _forward_label(m), **media_info})
    return out

async def quote_cmd(update, context):
    msg = update.effective_message
    p = qx.parse_args(context.args)
    rp, multi = msg.reply_to_message, None
    if p["n"] > 1:
        if rp:
            multi = qc.span(msg.chat_id, rp.message_id, p["n"], msg.message_id)
            if len(multi) < p["n"]:
                first = multi[0].message_id if multi else msg.message_id
                multi = qc.last(msg.chat_id, first, p["n"] - len(multi)) + multi
        else: multi = qc.last(msg.chat_id, msg.message_id, p["n"])
        if len(multi) < 2:
            await msg.reply_html("🌸 <b>Not enough messages yet, %s</b>\n<blockquote>I only remember messages sent since my last restart. Try again after a few more.</blockquote>" % qx._name(update.effective_user))
            return
        rp = rp or multi[0]
    if not rp:
        await msg.reply_text("💬 <b>Reply to a message</b>\n<blockquote>Use /q on any message, or /q r to include the message it replied to ✨</blockquote>", parse_mode=H)
        return
    rp = qc.get(update.effective_chat.id, rp.message_id) or rp
    if p["bad"]:
        await msg.reply_html("🎨 <b>Didn't know</b> <code>%s</code>\n<blockquote>Try /qcolors to see colour names.%s</blockquote>" % (_html.escape(p["bad"][0]), qx.hint(p["bad"][0])))
    msgs = multi or [rp]
    try:
        roles = await _roles(context.bot, msg.chat_id, msgs)
        msg_dicts = await _build_msg_dicts(context.bot, msgs, roles, include_reply=bool(p["r"]))
        if not msg_dicts:
            await msg.reply_text("🌸 <b>No content found</b>\n<blockquote>I couldn't find any text or media to quote 🌸</blockquote>", parse_mode=H)
            return
        img = await asyncio.to_thread(qp.render_messages, msg_dicts, p["color"])
        out = io.BytesIO()
        img.save(out, format="WEBP", lossless=True, quality=95)
        if out.tell() > 500_000:
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=85)
        out.seek(0); out.name = "quote.webp"
        await msg.reply_sticker(sticker=out, read_timeout=30, write_timeout=30, connect_timeout=30, pool_timeout=30)
    except Exception as e:
        await msg.reply_text(f"⚠️ <b>Couldn't make the sticker</b>\n<blockquote>{e}</blockquote>", parse_mode=H)

def register(app):
    app.add_handler(CommandHandler("q", quote_cmd))
    qc.register(app)

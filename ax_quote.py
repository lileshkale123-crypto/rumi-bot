"""/q quotes one message, /q r also shows the message it replied to."""
import asyncio
import html as _html
import io
import time

from telegram.constants import ParseMode
from telegram.ext import CommandHandler

import ax_qextra as qx
import quote_cache as qc
import quote_pil as qp

H = ParseMode.HTML
_ROLE_CACHE = {}
_AVATAR_CACHE = {}


async def _get_avatar(bot, user_id: int) -> bytes | None:
    if not user_id:
        return None
    now = time.time()
    hit = _AVATAR_CACHE.get(user_id)
    if hit and now - hit[0] < 300:
        return hit[1]
    avatar = None
    try:
        ph = await bot.get_user_profile_photos(user_id, limit=1)
        if ph.total_count:
            f = await bot.get_file(ph.photos[0][-1].file_id)
            buf = io.BytesIO()
            await f.download_to_memory(out=buf)
            avatar = buf.getvalue()
    except Exception as e:
        print("quote photo failed:", e)
    if avatar:
        _AVATAR_CACHE[user_id] = (now, avatar)
    return avatar


async def _roles(bot, chat_id: int, msgs: list) -> dict:
    out = {}
    now = time.time()
    for m in msgs:
        u = m.from_user
        if u is None or m.chat.type not in ("group", "supergroup"):
            continue
        key = (chat_id, u.id)
        hit = _ROLE_CACHE.get(key)
        if hit and now - hit[0] < 600:
            val = hit[1]
        else:
            val = None
            try:
                cm = await bot.get_chat_member(chat_id, u.id)
                if cm.status == "creator":
                    val = ("owner", getattr(cm, "custom_title", None))
                elif cm.status == "administrator":
                    val = ("admin", getattr(cm, "custom_title", None))
            except Exception as e:
                print("quote role failed:", repr(e)[:100])
            _ROLE_CACHE[key] = (now, val)
        if val:
            out[u.id] = val
    return out


async def _download_media(bot, m) -> tuple[bytes | None, str | None]:
    """Download optional photo or static sticker inside a quoted message."""
    try:
        if getattr(m, "photo", None):
            f = await bot.get_file(m.photo[-1].file_id)
            buf = io.BytesIO()
            await f.download_to_memory(out=buf)
            return buf.getvalue(), "photo"
        st = getattr(m, "sticker", None)
        if st:
            fid = (
                st.thumbnail.file_id
                if (st.is_animated or st.is_video) and getattr(st, "thumbnail", None)
                else st.file_id if not (st.is_animated or st.is_video) else None
            )
            if fid:
                f = await bot.get_file(fid)
                buf = io.BytesIO()
                await f.download_to_memory(out=buf)
                return buf.getvalue(), "sticker"
    except Exception as e:
        print("quote media download failed:", repr(e)[:100])
    return None, None


def _sender_info(m) -> tuple[str, int]:
    u = getattr(m, "from_user", None)
    if u:
        name = u.full_name or u.first_name or "User"
        return name, u.id
    c = getattr(m, "sender_chat", None)
    if c:
        return getattr(c, "title", None) or "Channel", c.id
    return "Someone", 0


async def _build_msg_dicts(bot, msgs: list, roles: dict, include_reply: bool) -> list[dict]:
    # Pre-fetch unique user avatars concurrently
    uids = { _sender_info(m)[1] for m in msgs if _sender_info(m)[1] > 0 }
    av_tasks = { uid: asyncio.create_task(_get_avatar(bot, uid)) for uid in uids }
    avatars = { uid: await t for uid, t in av_tasks.items() }

    out = []
    for idx, m in enumerate(msgs):
        text = m.text or m.caption or ""
        ents = (m.entities if m.text else m.caption_entities) or []
        ent_dicts = [e.to_dict() for e in ents]
        name, uid = _sender_info(m)

        media_bytes, media_type = await _download_media(bot, m)
        if not text and not media_bytes:
            continue

        reply_dict = None
        r = getattr(m, "reply_to_message", None)
        # Show reply preview when /q r is requested or when stacked messages reply inside the chain
        if r is not None and (include_reply or len(msgs) > 1):
            r_text = (r.text or r.caption or "").strip()
            if not r_text and getattr(r, "photo", None):
                r_text = "📷 Photo"
            elif not r_text and getattr(r, "sticker", None):
                r_text = "Sticker"
            if r_text:
                r_name, r_uid = _sender_info(r)
                reply_dict = {"name": r_name, "text": r_text, "user_id": r_uid}

        out.append({
            "name": name,
            "user_id": uid,
            "text": text,
            "entities": ent_dicts,
            "avatar": avatars.get(uid),
            "tag": roles.get(uid),
            "reply": reply_dict,
            "media": media_bytes,
            "media_type": media_type,
        })
    return out


async def quote_cmd(update, context):
    msg = update.effective_message
    p = qx.parse_args(context.args)
    rp = msg.reply_to_message
    multi = None
    if p["n"] > 1:
        if rp:
            multi = qc.span(msg.chat_id, rp.message_id, p["n"], msg.message_id)
            if len(multi) < p["n"]:
                first = multi[0].message_id if multi else msg.message_id
                multi = qc.last(msg.chat_id, first, p["n"] - len(multi)) + multi
        else:
            multi = qc.last(msg.chat_id, msg.message_id, p["n"])
        if len(multi) < 2:
            await msg.reply_html(
                "🌸 <b>Not enough messages yet, %s</b>\n<blockquote>I only remember "
                "messages sent since my last restart. Try again after a few more.</blockquote>"
                % qx._name(update.effective_user)
            )
            return
        rp = rp or multi[0]
    if not rp:
        await msg.reply_text(
            "💬 <b>Reply to a message</b>\n<blockquote>Use /q on any text message, or /q r to include the message it replied to ✨</blockquote>",
            parse_mode=H,
        )
        return
    rp = qc.get(update.effective_chat.id, rp.message_id) or rp
    want_reply = p["r"] and p["n"] == 1
    if p["bad"]:
        await msg.reply_html(
            "🎨 <b>Didn't know</b> <code>%s</code>\n<blockquote>Try /qcolors "
            "to see colour names.%s</blockquote>"
            % (_html.escape(p["bad"][0]), qx.hint(p["bad"][0]))
        )
    msgs = multi or [rp]
    try:
        valid_msgs = [
            m for m in msgs
            if (m.text or m.caption or getattr(m, "photo", None) or getattr(m, "sticker", None))
        ]
        if not valid_msgs:
            await msg.reply_text(
                "🌸 <b>No text found</b>\n<blockquote>I can only quote text messages 🌸</blockquote>",
                parse_mode=H,
            )
            return

        roles = await _roles(context.bot, msg.chat_id, valid_msgs)
        msg_dicts = await _build_msg_dicts(context.bot, valid_msgs, roles, include_reply=want_reply)
        if not msg_dicts:
            await msg.reply_text(
                "🌸 <b>No text found</b>\n<blockquote>I can only quote text messages 🌸</blockquote>",
                parse_mode=H,
            )
            return

        img = await asyncio.to_thread(qp.render_messages, msg_dicts, p["color"])
        out = io.BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        await msg.reply_sticker(
            sticker=out,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
        )
    except Exception as e:
        await msg.reply_text(
            f"⚠️ <b>Couldn't make the sticker</b>\n<blockquote>{e}</blockquote>",
            parse_mode=H,
        )


def register(app):
    app.add_handler(CommandHandler("q", quote_cmd))
    qc.register(app)


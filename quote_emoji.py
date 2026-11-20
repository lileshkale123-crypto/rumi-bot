"""Helpers for /q: fancy letters -> plain, emoji -> Google/Twemoji pictures (async prefetch + safe cache)."""
import asyncio
import io
import os
import re
import threading
import unicodedata
from collections import OrderedDict

import httpx
from PIL import Image

import quote_fb as qf

CACHE_DIR = "emoji_cache_g"
URL = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{}.png"
GURL = "https://cdn.jsdelivr.net/npm/emoji-datasource-google@15.0.1/img/google/64/{}.png"

_MEM_MAX = 512
_mem: OrderedDict[str, Image.Image | None] = OrderedDict()
_lock = threading.RLock()
_sync_client: httpx.Client | None = None
_async_client: httpx.AsyncClient | None = None

SMALL_CAPS = dict(zip("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ", "abcdefghijklmnopqrstuvwxyz"))

_MOD = r"[\uFE0F\U0001F3FB-\U0001F3FF]?"
_BASE = (
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF"
    r"\u203C\u2049\u2122\u2139\u24C2\u25AA-\u25FE\u2934\u2935"
    r"\u3030\u303D\u3297\u3299\u00A9\u00AE]"
)
EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[0-9#*]\uFE0F?\u20E3"
    r"|" + _BASE + _MOD + r"(?:\u200D" + _BASE + _MOD + r")*"
)


def _get_sync_client() -> httpx.Client:
    global _sync_client
    with _lock:
        if _sync_client is None or _sync_client.is_closed:
            _sync_client = httpx.Client(timeout=6.0)
        return _sync_client


def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=4.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _async_client


async def close_clients() -> None:
    global _async_client, _sync_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
    _async_client = None
    with _lock:
        if _sync_client is not None and not _sync_client.is_closed:
            _sync_client.close()
        _sync_client = None


def clean(text: str) -> str:
    """Fancy letters the font cannot draw -> plain letters."""
    out = []
    for c in text:
        o = ord(c)
        if c in SMALL_CAPS:
            out.append(SMALL_CAPS[c])
        elif 0x1D400 <= o <= 0x1D7FF or 0xFF01 <= o <= 0xFF5E or 0x24B6 <= o <= 0x24E9:
            out.append(c if qf.covered(c) else unicodedata.normalize("NFKC", c))
        else:
            out.append(c)
    return "".join(out)


def split(text: str) -> list[tuple[str, str]]:
    """Returns a list of ('t', text) and ('e', emoji) pieces."""
    parts, pos = [], 0
    for m in EMOJI_RE.finditer(text):
        if m.start() > pos:
            parts.append(("t", text[pos : m.start()]))
        parts.append(("e", m.group()))
        pos = m.end()
    if pos < len(text):
        parts.append(("t", text[pos:]))
    return parts


def _name(e: str) -> str:
    if "\u200d" not in e:
        e = e.replace("\ufe0f", "")
    return "-".join(f"{ord(c):x}" for c in e)


def _mem_put(key: str, img: Image.Image | None) -> None:
    with _lock:
        _mem[key] = img
        _mem.move_to_end(key)
        while len(_mem) > _MEM_MAX:
            _mem.popitem(last=False)


def _save_cache_atomic(path: str, data: bytes) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


async def _prefetch_one(e: str) -> None:
    key = _name(e)
    with _lock:
        if key in _mem:
            return
    path = os.path.join(CACHE_DIR, key + ".png")
    if os.path.isfile(path):
        return
    client = _get_async_client()
    gname = "-".join(f"{ord(c):x}" for c in e)
    data = None
    for u in (GURL.format(gname), GURL.format(key), URL.format(key)):
        try:
            r = await client.get(u)
            if r.status_code == 200 and r.content:
                data = r.content
                break
        except Exception:
            pass
    if data is not None:
        await asyncio.to_thread(_save_cache_atomic, path, data)


async def prefetch(texts: list[str]) -> None:
    """Download any uncached emoji PNGs asynchronously before PIL renders in a worker thread."""
    emojis: set[str] = set()
    for t in texts:
        if not t:
            continue
        for m in EMOJI_RE.finditer(t):
            emojis.add(m.group())
    if not emojis:
        return
    await asyncio.gather(*(_prefetch_one(e) for e in emojis), return_exceptions=True)


def fetch(e: str) -> Image.Image | None:
    """Emoji picture (RGBA) or None. Downloaded once, then cached on disk and in bounded RAM."""
    key = _name(e)
    with _lock:
        if key in _mem:
            _mem.move_to_end(key)
            return _mem[key]

    path = os.path.join(CACHE_DIR, key + ".png")
    img = None
    try:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            data = None
            gname = "-".join(f"{ord(c):x}" for c in e)
            client = _get_sync_client()
            for u in (GURL.format(gname), GURL.format(key), URL.format(key)):
                try:
                    r = client.get(u)
                    if r.status_code == 200 and r.content:
                        data = r.content
                        break
                except Exception:
                    pass
            if data is None:
                raise ValueError("no emoji picture")
            _save_cache_atomic(path, data)
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        img = None

    _mem_put(key, img)
    return img

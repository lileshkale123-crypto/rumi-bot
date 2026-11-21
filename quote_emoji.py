"""Helpers for /q: fancy letters -> plain, Apple emoji pictures (disk cache + async prefetch).

Rules this module follows:
* The render thread NEVER touches the network. fetch() reads the disk cache only.
* Missing pictures are downloaded by prefetch() (async, before rendering) and misses are
  remembered, so a missing emoji is not retried on every request.
* Everything is bounded in size. Pictures are global (an emoji looks the same for every
  bot), so nothing here is per-bot data.
"""
import asyncio
import io
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict

import httpx
from PIL import Image

import quote_fb as qf

CACHE_DIR = "emoji_cache_apple"
# Older Google/Twemoji pictures: only used when the Apple picture is not on disk yet.
LEGACY_DIRS = ("emoji_cache_g", "emoji_cache")
CDN_VERSION = "16.0.0"
CDN_URL = "https://cdn.jsdelivr.net/npm/emoji-datasource-apple@" + CDN_VERSION + "/img/apple/64/{}.png"
COMPLETE_MARK = os.path.join(CACHE_DIR, ".complete")  # written by fetch_quote_assets.py

_MEM_MAX = 512
_MISS_MAX = 4000
_MISS_TTL_ABSENT = 86400  # every URL said 404: the emoji has no picture
_MISS_TTL_ERROR = 300  # network trouble: try again soon
_PREFETCH_PARALLEL = 8

_mem: OrderedDict[str, Image.Image | None] = OrderedDict()
_miss: OrderedDict[str, float] = OrderedDict()
_index: dict[str, str] | None = None
_lock = threading.RLock()
_async_client: httpx.AsyncClient | None = None
_sem: asyncio.Semaphore | None = None
_net_down_until = 0.0

SMALL_CAPS = dict(zip("\U00001D00\U00000299\U00001D04\U00001D05\U00001D07\U0000A730\U00000262\U0000029C\U0000026A\U00001D0A\U00001D0B\U0000029F\U00001D0D\U00000274\U00001D0F\U00001D18\U000001EB\U00000280\U0000A731\U00001D1B\U00001D1C\U00001D20\U00001D21x\U0000028F\U00001D22", "abcdefghijklmnopqrstuvwxyz"))

# ---- What counts as an emoji ------------------------------------------------
_MOD = r"[\U0001F3FB-\U0001F3FF]"
# always shown as a picture
_EP = (
    r"[\U0001F300-\U0001FAFF\U0000231A\U0000231B\U000023E9-\U000023EC\U000023F0\U000023F3\U000025FD\U000025FE\U00002614\U00002615"
    r"\U00002648-\U00002653\U0000267F\U00002693\U000026A1\U000026AA\U000026AB\U000026BD\U000026BE\U000026C4\U000026C5\U000026CE\U000026D4\U000026EA"
    r"\U000026F2\U000026F3\U000026F5\U000026FA\U000026FD\U00002705\U0000270A\U0000270B\U00002728\U0000274C\U0000274E\U00002753-\U00002755\U00002757"
    r"\U00002795-\U00002797\U000027B0\U000027BF\U00002B1B\U00002B1C\U00002B50\U00002B55]"
)
# plain text symbols: only an emoji when followed by the emoji marker U+FE0F
_TV = (
    r"[\U000000A9\U000000AE\U0000203C\U00002049\U00002122\U00002139\U00002194-\U00002199\U000021A9\U000021AA\U00002328\U000023CF\U000023ED-\U000023EF"
    r"\U000023F1\U000023F2\U000023F8-\U000023FA\U000024C2\U000025AA\U000025AB\U000025B6\U000025C0\U000025FB\U000025FC\U00002934\U00002935"
    r"\U00002B05-\U00002B07\U00003030\U0000303D\U00003297\U00003299]"
)
# symbols people type bare and expect as a picture (hearts, weather, cards ...): marker optional
_OTHER = (
    r"[\U00002600-\U00002604\U0000260E\U00002611\U00002618\U0000261D\U00002620\U00002622\U00002623\U00002626\U0000262A\U0000262E\U0000262F"
    r"\U00002638-\U0000263A\U00002640\U00002642\U0000265F\U00002660\U00002663\U00002665\U00002666\U00002668\U0000267B\U0000267E"
    r"\U00002692\U00002694-\U00002697\U00002699\U0000269B\U0000269C\U000026A0\U000026A7\U000026B0\U000026B1\U000026C8\U000026CF\U000026D1\U000026D3"
    r"\U000026E9\U000026F0\U000026F1\U000026F4\U000026F7-\U000026F9\U00002702\U00002708\U00002709\U0000270C\U0000270D\U0000270F\U00002712"
    r"\U00002714\U00002716\U0000271D\U00002721\U00002733\U00002734\U00002744\U00002747\U00002763\U00002764\U000027A1]"
)
_ANY = "(?:" + _EP + "|" + _TV + "|" + _OTHER + ")"
_FIRST = (
    "(?:" + _EP + r")(?:\U0000FE0F|" + _MOD + ")?"
    "|(?:" + _TV + r")\U0000FE0F"
    "|(?:(?!" + _TV + ")" + _OTHER + r")(?:\U0000FE0F|" + _MOD + ")?"
)
_CONT = _ANY + r"(?:\U0000FE0F|" + _MOD + ")?"
EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"  # flags
    r"|\U0001F3F4[\U000E0020-\U000E007E]+\U000E007F"  # england / scotland / wales flags
    r"|[0-9#*]\U0000FE0F?\U000020E3"  # keycaps
    r"|(?:" + _FIRST + r")(?:\U0000200D(?:" + _CONT + "))*"
)

_VS16 = "\U0000FE0F"
_ZWJ = "\U0000200D"


def _key(e: str) -> str:
    return "-".join(f"{ord(c):x}" for c in e if c != _VS16)


def _candidates(e: str) -> list[str]:
    """File names (without .png) to try, most likely first."""
    cps = list(e)
    out: list[str] = []

    def add(n: str) -> None:
        if n and n not in out:
            out.append(n)

    add("-".join(f"{ord(c):x}" for c in cps))
    add(_key(e))
    base = [c for c in cps if c != _VS16]

    def qualify(bmp_only: bool) -> str:
        r = []
        for i, c in enumerate(base):
            o = ord(c)
            r.append(f"{o:x}")
            if c == _ZWJ or c == "\U000020E3" or 0x1F3FB <= o <= 0x1F3FF:
                continue
            if 0x1F1E6 <= o <= 0x1F1FF or 0xE0020 <= o <= 0xE007F:
                continue
            nxt = base[i + 1] if i + 1 < len(base) else ""
            if nxt and 0x1F3FB <= ord(nxt) <= 0x1F3FF:
                continue
            if bmp_only and o > 0xFFFF:
                continue
            r.append("fe0f")
        return "-".join(r)

    add(qualify(True))
    add(qualify(False))
    return out


def _load_index() -> dict[str, str]:
    global _index
    with _lock:
        if _index is None:
            idx: dict[str, str] = {}
            try:
                for fn in os.listdir(CACHE_DIR):
                    if fn.endswith(".png"):
                        name = fn[:-4]
                        idx[name.replace("-fe0f", "")] = name
            except OSError:
                pass
            _index = idx
        return _index


def _index_add(name: str) -> None:
    key = name.replace("-fe0f", "")
    with _lock:
        _load_index()[key] = name
        _mem.pop(key, None)  # drop an older (legacy) picture so the Apple one is used


def _mem_put(key: str, img: Image.Image | None) -> None:
    with _lock:
        _mem[key] = img
        _mem.move_to_end(key)
        while len(_mem) > _MEM_MAX:
            _mem.popitem(last=False)


def _miss_put(key: str, ttl: float) -> None:
    with _lock:
        _miss[key] = time.time() + ttl
        _miss.move_to_end(key)
        while len(_miss) > _MISS_MAX:
            _miss.popitem(last=False)


def _is_missing(key: str) -> bool:
    with _lock:
        until = _miss.get(key)
        if until is None:
            return False
        if until < time.time():
            _miss.pop(key, None)
            return False
        return True


def _save_atomic(path: str, data: bytes) -> bool:
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        return False


def _decode(data: bytes) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.load()
        return img
    except Exception:
        return None


def _find_on_disk(e: str) -> bytes | None:
    """Apple picture first, then the old Google/Twemoji pictures."""
    name = _load_index().get(_key(e))
    if name:
        try:
            with open(os.path.join(CACHE_DIR, name + ".png"), "rb") as fh:
                return fh.read()
        except OSError:
            pass
    for d in LEGACY_DIRS:
        for c in _candidates(e):
            p = os.path.join(d, c + ".png")
            if os.path.isfile(p):
                try:
                    with open(p, "rb") as fh:
                        return fh.read()
                except OSError:
                    continue
    return None


def fetch(e: str) -> Image.Image | None:
    """Emoji picture (RGBA) or None. Disk and RAM only, never the network."""
    key = _key(e)
    with _lock:
        if key in _mem:
            _mem.move_to_end(key)
            return _mem[key]
    img = None
    data = _find_on_disk(e)
    if data:
        img = _decode(data)
    if img is not None:
        _mem_put(key, img)
    return img


def _have(e: str) -> bool:
    """True when the Apple picture is on disk."""
    return _key(e) in _load_index()


# ---- Async download (runs before rendering, never inside the render thread) --

def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _async_client


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_PREFETCH_PARALLEL)
    return _sem


async def close_clients() -> None:
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
    _async_client = None


async def _prefetch_one(e: str) -> None:
    key = _key(e)
    if _have(e) or _is_missing(key):
        return
    async with _get_sem():
        if _have(e):
            return
        client = _get_async_client()
        only_404 = True
        for cand in _candidates(e):
            try:
                r = await client.get(CDN_URL.format(cand))
            except Exception:
                only_404 = False
                continue
            if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n":
                path = os.path.join(CACHE_DIR, cand + ".png")
                if await asyncio.to_thread(_save_atomic, path, r.content):
                    _index_add(cand)
                return
            if r.status_code != 404:
                only_404 = False
        _miss_put(key, _MISS_TTL_ABSENT if only_404 else _MISS_TTL_ERROR)


async def prefetch(texts: list[str]) -> None:
    """Download any emoji pictures that are not on disk yet. Safe to call with any text."""
    emojis: set[str] = set()
    for t in texts:
        if t:
            emojis.update(m.group() for m in EMOJI_RE.finditer(t))
    if not emojis:
        return
    _load_index()
    todo = [e for e in emojis if not _have(e) and not _is_missing(_key(e))]
    if not todo:
        return
    if os.path.isfile(COMPLETE_MARK):
        # the full Apple set is on disk: whatever is missing simply has no picture
        for e in todo:
            _miss_put(_key(e), _MISS_TTL_ABSENT)
        return
    global _net_down_until
    if time.time() < _net_down_until:
        return  # the CDN was unreachable a moment ago: do not make users wait again
    try:
        await asyncio.wait_for(asyncio.gather(*(_prefetch_one(e) for e in todo), return_exceptions=True), timeout=6.0)
    except asyncio.TimeoutError:
        _net_down_until = time.time() + 120


# ---- Text helpers -------------------------------------------------------------

def clean(text: str) -> str:
    """Fancy letters the fonts cannot draw -> plain letters. Letters a font can draw are kept."""
    out = []
    for c in text:
        o = ord(c)
        if c in SMALL_CAPS:
            out.append(c if qf.covered(c) else SMALL_CAPS[c])
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

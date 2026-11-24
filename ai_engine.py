"""Gemini + Groq AI chat engine with shared async HTTP pooling and sequential fallback."""
import asyncio
import logging
import os
import pathlib

import httpx

import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Rumi, a playful, witty girl who chats on Telegram.
Style: short, casual replies (1-3 sentences), relaxed Hinglish or English to match the user,
a few emojis now and then, never robotic, never a long lecture unless asked.
You are an AI and never pretend to be human. If someone flirts or asks you out,
deflect with a joke and keep it friendly. You can help with studying, coding and
general questions. Keep things kind and safe; refuse anything harmful politely.
You know nothing about anyone's coins or wallets and cannot change them."""

# Load persona from persona.txt (edit that file, restart the bot)
_persona = pathlib.Path(__file__).with_name("persona.txt")
if _persona.exists():
    SYSTEM_PROMPT = _persona.read_text(encoding="utf-8").strip()

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32, keepalive_expiry=60.0),
        )
    return _http_client


async def close_client() -> None:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


async def on_shutdown() -> None:
    await close_client()


class GeminiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Gemini error {status}: {body}")
        self.status = status


_blocked_until = 0.0  # monotonic time until which Gemini is skipped (quota used up)
BLOCK_SECONDS = 90.0


def _gemini_keys() -> list[str]:
    """GEMINI_API_KEY may hold one key or several keys separated by commas."""
    raw = os.environ.get("GEMINI_API_KEY", "")
    return [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]


async def gemini_chat(
    history: list[tuple[str, str]],
    text: str,
    model: str | None = None,
    key: str | None = None,
) -> str | None:
    """history = [(role, text)] with role 'user' or 'model'. Returns reply text or None."""
    contents = [{"role": r, "parts": [{"text": t}]} for r, t in history]
    contents.append({"role": "user", "parts": [{"text": text}]})

    gen = {"maxOutputTokens": config.AI_MAX_TOKENS, "temperature": 0.9}
    if config.AI_THINKING_BUDGET is not None:
        gen["thinkingConfig"] = {"thinkingBudget": config.AI_THINKING_BUDGET}

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": gen,
    }

    if key is None:
        keys = _gemini_keys()
        if not keys:
            raise GeminiError(401, "GEMINI_API_KEY not set")
        key = keys[0]

    headers = {"x-goog-api-key": key}
    client = _get_client()
    r = await client.post(URL.format(model=model or config.AI_MODEL), json=body, headers=headers)
    if r.status_code != 200:
        raise GeminiError(r.status_code, r.text[:200])

    try:
        data = r.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip() or None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def _gemini_chat_all(history: list[tuple[str, str]], text: str) -> str | None:
    """Tries every key and every model in config.AI_MODELS. When all fail only because the
    quota is used up (429), Gemini is skipped for a short while so the backup answers fast."""
    global _blocked_until
    import time

    if time.monotonic() < _blocked_until:
        raise GeminiError(429, "gemini cooling down")
    keys = _gemini_keys()
    if not keys:
        raise GeminiError(401, "GEMINI_API_KEY not set")
    last: Exception | None = None
    only_quota = True
    for key in keys:
        for model in config.AI_MODELS:
            for attempt in range(2):
                try:
                    out = await gemini_chat(history, text, model, key)
                    if out:
                        return out
                    only_quota = False
                    break
                except GeminiError as e:
                    last = e
                    log.warning("model %s failed: status %s %s", model, e.status, str(e)[:120])
                    if e.status != 429:
                        only_quota = False
                    if e.status == 429:
                        break
                    if e.status in (500, 502, 503, 504) and attempt == 0:
                        await asyncio.sleep(1)
                        continue
                    break
                except httpx.HTTPError as e:
                    last = e
                    only_quota = False
                    log.warning("model %s HTTP error: %r", model, e)
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    break
    if last is not None:
        if only_quota:
            _blocked_until = time.monotonic() + BLOCK_SECONDS
        raise last
    return None


async def groq_chat(
    history: list[tuple[str, str]],
    text: str,
    model: str,
) -> str | None:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for r, t in history:
        msgs.append({"role": "assistant" if r == "model" else "user", "content": t})
    msgs.append({"role": "user", "content": text})

    body = {
        "model": model,
        "messages": msgs,
        "temperature": 0.9,
        "max_tokens": config.AI_MAX_TOKENS + 300,
    }
    client = _get_client()
    r = await client.post(GROQ_URL, json=body, headers={"Authorization": "Bearer " + key})
    if r.status_code != 200:
        raise GeminiError(r.status_code, r.text[:200])

    try:
        out = r.json()["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return out.strip() or None


async def chat(history: list[tuple[str, str]], text: str) -> str:
    last: Exception = RuntimeError("no reply")
    try:
        out = await _gemini_chat_all(history, text)
        if out:
            return out
    except Exception as ex:
        last = ex
        log.warning("all Gemini models failed, trying Groq")

    if not os.environ.get("GROQ_API_KEY"):
        log.warning("GROQ_API_KEY is not set, so there is no backup AI")
    for model in config.GROQ_MODELS:
        try:
            out = await groq_chat(history, text, model)
            if out:
                return out
        except Exception as ex:
            last = ex
            log.warning("groq model %s failed: %s", model, getattr(ex, "status", ex))

    raise last


async def status() -> list[str]:
    """One line per model: OK or the error. Used by /aistatus (owner only)."""
    import time

    lines: list[str] = []
    keys = _gemini_keys()
    left = _blocked_until - time.monotonic()
    lines.append("Gemini keys: %d%s" % (len(keys), "  (cooling %ds)" % left if left > 0 else ""))
    lines.append("Groq key: %s" % ("set" if os.environ.get("GROQ_API_KEY") else "NOT SET"))

    async def one(label: str, coro) -> str:
        try:
            out = await asyncio.wait_for(coro, 20.0)
            return "%s: OK" % label if out else "%s: empty reply" % label
        except GeminiError as e:
            body = str(e).split(": ", 1)[-1].replace("\n", " ")
            return "%s: %s %s" % (label, e.status, body[:70])
        except Exception as e:
            return "%s: %s" % (label, type(e).__name__)

    jobs = []
    for i, key in enumerate(keys[:3]):
        for model in config.AI_MODELS:
            jobs.append(one("G%d %s" % (i + 1, model), gemini_chat([], "say hi", model, key)))
    if os.environ.get("GROQ_API_KEY"):
        for model in config.GROQ_MODELS:
            jobs.append(one("Groq %s" % model, groq_chat([], "say hi", model)))
    lines.extend(await asyncio.gather(*jobs))
    return lines

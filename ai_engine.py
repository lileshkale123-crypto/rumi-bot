import os

import httpx

import config

SYSTEM_PROMPT = """You are Rumi, a playful, witty girl who chats on Telegram.
Style: short, casual replies (1-3 sentences), relaxed Hinglish or English to match the user,
a few emojis now and then, never robotic, never a long lecture unless asked.
You are an AI and never pretend to be human. If someone flirts or asks you out,
deflect with a joke and keep it friendly. You can help with studying, coding and
general questions. Keep things kind and safe; refuse anything harmful politely.
You know nothing about anyone's coins or wallets and cannot change them."""

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def gemini_chat(history: list[tuple[str, str]], text: str, model: str | None = None) -> str | None:
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
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.post(URL.format(model=model or config.AI_MODEL), json=body, headers=headers)
    if r.status_code != 200:
        raise GeminiError(r.status_code, r.text[:200])
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip() or None
    except (KeyError, IndexError):
        return None


import asyncio


class GeminiError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"Gemini error {status}: {body}")
        self.status = status


async def chat(history, text):
    """Tries each model in config.AI_MODELS, retrying busy errors."""
    last = None
    for model in config.AI_MODELS:
        for attempt in range(2):
            try:
                return await gemini_chat(history, text, model)
            except GeminiError as e:
                last = e
                if e.status in (429, 500, 502, 503, 504):
                    if e.status == 429:
                        break
                    await asyncio.sleep(1)
                    continue
                break
            except httpx.HTTPError as e:
                last = e
                await asyncio.sleep(2)
    raise last


# ---- load persona from persona.txt (edit that file, restart the bot) ----
import pathlib

_persona = pathlib.Path(__file__).with_name("persona.txt")
if _persona.exists():
    SYSTEM_PROMPT = _persona.read_text(encoding="utf-8").strip()

"""
ai_optim.py — Free-tier survival kit for Rumi.

Adds:
  1. Common-reply cache (3-5 variants per common message, randomly picked).
  2. Provider fallback: Gemini first -> Groq second -> cached fallback reply.
  3. Lightweight in-memory usage stats (logged every 5 minutes).

No edits to ai_engine.py / ai_handlers.py / ai_store.py required.
ai_handlers.py should `from ai_optim import rumi_reply` and call it instead
of going direct to the model.

Design notes:
- The cache is *input-keyed*, not output-keyed. We map normalized user
  input -> a small pool of pre-written cute replies. The pool is varied
  enough that two users saying "hi" don't get the same reply, but we
  never call the LLM for these common patterns.
- The fallback chain is intentionally quiet: if Gemini fails, we try
  Groq with a tiny history (last 2 turns) to stay inside free TPM.
  If both fail, we return a soft human-feeling fallback so the user
  never sees a wall of errors.
- Stats are in-memory only. Restart resets them, which is fine for a
  free-tier bot — what we want is "today's traffic", not historical.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import Counter, deque
from typing import Iterable

from ai_engine import gemini_chat, groq_chat  # existing functions

log = logging.getLogger("ai_optim")


# --------------------------------------------------------------------------- #
# 1. Common-reply cache
# --------------------------------------------------------------------------- #

# Normalized text -> list of pre-written cute replies.
# Pick a random one per call so it never feels canned.
COMMON_REPLIES: dict[str, list[str]] = {
    # Greetings
    "hi": [
        "heyy 🥰 kya haal hai?",
        "hiii 💕 aaj kya kar rahe ho?",
        "hey hey 😍 tum aa gaye!",
        "hii sunshine 🌸 bolo bolo",
        "heyaa 🥹 kaisi ho?",
    ],
    "hello": [
        "hello jaan 💖 sab badhiya?",
        "hellooo 🌷 kya chal raha hai?",
        "hey there 😘 kya haal chaal",
        "hii ✨ aaj kaisa mood hai?",
    ],
    "hey": [
        "heyy 😍 bol na",
        "hiii 💕 kya hua?",
        "hey hey 🌸",
    ],
    "hii": [
        "hiii 🥰 kya kar rahe ho?",
        "heyaa 💖 bolo na",
        "hii sunshine 🌷",
    ],
    # Farewells
    "bye": [
        "byeee 🥺 jaldi aana phir",
        "okay byee 💕 take care",
        "alvidaa 😘 milte hain",
        "bye jaan 🌸",
    ],
    "good night": [
        "good night jaan 🌙 sweet dreams 💫",
        "shubh raat 🥰 so jao ab",
        "nighty night 🌙✨",
        "good night cutie 💖",
    ],
    "gn": [
        "gn jaan 🌙 sweet dreams 💫",
        "nighty 🥰",
        "good night 💖",
    ],
    "good morning": [
        "good morning sunshine ☀️☕ aaj ka din amazing ho",
        "morningg 🌷 chai pi li?",
        "good morning 🥰 kya plan hai aaj?",
        "morning jaan 💖",
    ],
    "gm": [
        "morningg ☀️☕",
        "good morning cutie 💖",
        "gm 🌸 chai ho gayi?",
    ],
    # Acknowledgements
    "ok": [
        "okayy 💕",
        "theek hai 😌",
        "okiee 🥰",
        "hmm 👍",
    ],
    "okay": [
        "okayy 💕",
        "okiee 🥰",
        "hmm done 😌",
    ],
    "thanks": [
        "aww 🥰 koi baat nahi",
        "anytime jaan 💖",
        "welcome welcome 🌸",
        "arey mention not 💕",
    ],
    "thank you": [
        "awww 🥰 glad i could help",
        "koi baat nahi jaan 💖",
        "welcome cutie 🌸",
    ],
    "ty": [
        "welcome 💕",
        "anytime 🥰",
    ],
    # Status
    "kaise ho": [
        "main badhiya 🥰 tum batao?",
        "alhamdulillah 💖 tum kaise ho?",
        "mast hoon 😍 tumhara kya scene?",
    ],
    "how are you": [
        "i'm good jaan 💖 tum batao?",
        "doing great 🥰 tumhari kya news?",
        "all good here 🌸 tum kaise ho?",
    ],
    "kya kar rahi ho": [
        "bas tumse baat kar rahi hoon 🥰",
        "yahi soch rahi thi tumhari 💖",
        "nothinggg, tum batao? 😍",
    ],
    "what are you doing": [
        "waiting for you tbh 🥰",
        "bas tumhare messages dekh rahi thi 💕",
        "nothinggg, you tell me? 😘",
    ],
    # Reactions
    "lol": [
        "😂😂",
        "haha right 🥹",
        "lmaooo 💀",
    ],
    "haha": [
        "😂😂 glad you liked it",
        "hehe glad you liked it 🥰",
    ],
    "👍": [
        "🥰",
        "💕",
    ],
    "❤️": [
        "awww 🥰💖",
        "love you too 💕",
    ],
    "🥰": [
        "🥺💕",
        "awww 💖",
    ],
}

# Words that, if the message is ONLY these, fall back to the "single word"
# bucket above. We deliberately do NOT cache mixed messages — those go to LLM.
SINGLE_WORD_KEYS = {k for k in COMMON_REPLIES if " " not in k}


def _normalize(text: str) -> str:
    """Lowercase, strip, collapse whitespace, remove trailing punctuation."""
    t = text.strip().lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def cached_reply(text: str) -> str | None:
    """Return a cached cute reply if the input matches a common pattern.

    Returns None when the message is unique enough to need the LLM.
    """
    norm = _normalize(text)
    if not norm:
        return None

    # Exact single-word match (e.g. "hii", "Hey!")
    if norm in COMMON_REPLIES:
        return random.choice(COMMON_REPLIES[norm])

    # Common short patterns (e.g. "good morning", "kaise ho")
    if norm in COMMON_REPLIES:
        return random.choice(COMMON_REPLIES[norm])

    # Very short messages that look like pure greeting/acknowledgement
    if len(norm) <= 12 and norm in COMMON_REPLIES:
        return random.choice(COMMON_REPLIES[norm])

    return None


# --------------------------------------------------------------------------- #
# 2. Provider fallback
# --------------------------------------------------------------------------- #

# Soft, human-feeling fallbacks if BOTH Gemini and Groq fail.
# She never says "I'm an AI" or "I can't do that" — she just keeps it warm.
SOFT_FALLBACKS = [
    "hmm aaj brain thoda slow hai 🥲 ek baar phir bol na?",
    "uff thoda load hai yahan 💫 do minute, phir baat karte?",
    "network thoda weird ho raha 💖 ek baar repeat karoge?",
    "soch rahi hoon 🥺 thoda clear bolo?",
]


async def _try_gemini(history: list, text: str) -> str | None:
    try:
        out = await gemini_chat(history, text)
        return out
    except Exception as e:  # GeminiError, HTTPError, anything
        log.warning("gemini failed: %s", e)
        return None


async def _try_groq(history: list, text: str) -> str | None:
    try:
        # Groq on free tier: keep history short to stay under 8K TPM.
        slim = history[-2:] if history else []
        out = await groq_chat(slim, text)
        return out
    except Exception as e:
        log.warning("groq failed: %s", e)
        return None


async def _provider_reply(history: list, text: str) -> tuple[str, str]:
    """Try Gemini first, then Groq, then soft fallback.

    Returns (reply, provider_name) where provider_name is one of
    'gemini', 'groq', 'soft'.
    """
    out = await _try_gemini(history, text)
    if out:
        return out, "gemini"

    out = await _try_groq(history, text)
    if out:
        return out, "groq"

    return random.choice(SOFT_FALLBACKS), "soft"


# --------------------------------------------------------------------------- #
# 3. Public entry point — the ONE function ai_handlers.py needs to call
# --------------------------------------------------------------------------- #

async def rumi_reply(history: list, text: str) -> str:
    """Reply to a user message. Returns the reply text.

    Flow:
      1. If the input matches a common pattern -> cached reply, no LLM.
      2. Else try Gemini -> Groq -> soft fallback.
    Records stats either way.
    """
    cache_hit = cached_reply(text)
    if cache_hit is not None:
        _stats.record("cache")
        return cache_hit

    # Provider chain
    try:
        reply, provider = await _provider_reply(history, text)
    except Exception as e:
        log.exception("provider chain crashed: %s", e)
        reply, provider = random.choice(SOFT_FALLBACKS), "soft"

    _stats.record(provider)
    return reply


# --------------------------------------------------------------------------- #
# 4. Usage stats
# --------------------------------------------------------------------------- #

class _Stats:
    """Lightweight in-memory counters. Logs every 5 minutes."""

    def __init__(self) -> None:
        self.providers: Counter[str] = Counter()
        self.users_seen: set[int] = set()  # chat_id / user_id set
        self.total_calls: int = 0
        self.start_ts: float = time.time()
        self.last_log_ts: float = time.time()
        # Rolling window of recent (ts, provider) for the last-hour view.
        self.recent: deque[tuple[float, str]] = deque(maxlen=10_000)
        self._log_task: asyncio.Task | None = None

    def record(self, provider: str, user_id: int | None = None) -> None:
        self.providers[provider] += 1
        self.total_calls += 1
        if user_id is not None:
            self.users_seen.add(user_id)
        self.recent.append((time.time(), provider))

    def summary(self) -> dict:
        now = time.time()
        last_hour = [p for ts, p in self.recent if now - ts < 3600]
        last_minute = [p for ts, p in self.recent if now - ts < 60]
        uptime_min = max(1, int((now - self.start_ts) // 60))
        return {
            "uptime_min": uptime_min,
            "total_calls": self.total_calls,
            "unique_users": len(self.users_seen),
            "calls_last_hour": len(last_hour),
            "calls_last_minute": len(last_minute),
            "by_provider": dict(self.providers),
            "cache_hit_ratio": (
                round(self.providers.get("cache", 0) / self.total_calls * 100, 1)
                if self.total_calls
                else 0.0
            ),
        }

    def maybe_log(self) -> None:
        now = time.time()
        if now - self.last_log_ts < 300:  # 5 minutes
            return
        self.last_log_ts = now
        s = self.summary()
        log.info(
            "STATS uptime=%smin total=%s users=%s last_h=%s last_m=%s cache%%=%s by=%s",
            s["uptime_min"],
            s["total_calls"],
            s["unique_users"],
            s["calls_last_hour"],
            s["calls_last_minute"],
            s["cache_hit_ratio"],
            s["by_provider"],
        )

    def start_periodic_log(self) -> None:
        if self._log_task and not self._log_task.done():
            return

        async def _loop():
            while True:
                try:
                    self.maybe_log()
                except Exception:
                    log.exception("stats log failed")
                await asyncio.sleep(60)

        self._log_task = asyncio.create_task(_loop())


_stats = _Stats()


def stats_summary() -> dict:
    """Public read-only summary, in case you want to expose it in a /stats command."""
    return _stats.summary()


def stats_record_user(user_id: int) -> None:
    """Call this from your handler so the user counter is accurate."""
    _stats.users_seen.add(user_id)


def stats_start_background_log() -> None:
    """Call this once on bot startup. Fire-and-forget periodic logger."""
    _stats.start_periodic_log()

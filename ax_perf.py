"""Speed tools: (1) logs where the time goes for every group command, (2) keeps the
connection to Telegram warm so the first call after a quiet moment is not slow.
Turn the log off with the environment variable RUMI_PERF=0."""
import contextvars
import os
import time

from telegram.ext import TypeHandler
from telegram import Update
from telegram.request import HTTPXRequest

LOG = os.environ.get("RUMI_PERF", "1") != "0"
_ctx = contextvars.ContextVar("rumi_perf", default=None)
_patched = [False]


async def _mark(update, context):
    m, chat = update.effective_message, update.effective_chat
    if m is not None and chat is not None and chat.type != "private" and (m.text or "").startswith("/"):
        _ctx.set((time.perf_counter(), m.text.split()[0].split("@")[0][:24]))


def _patch():
    if _patched[0]:
        return
    _patched[0] = True
    orig = HTTPXRequest.do_request

    async def timed(self, url, method, *a, **k):
        c = _ctx.get()
        if c is None:
            return await orig(self, url, method, *a, **k)
        start = time.perf_counter()
        try:
            return await orig(self, url, method, *a, **k)
        finally:
            end = time.perf_counter()
            print("[perf] %s  +%4.0fms before %s  (call took %.0fms)" % (
                c[1], (start - c[0]) * 1000, str(url).rsplit("/", 1)[-1], (end - start) * 1000), flush=True)
    HTTPXRequest.do_request = timed


async def _ping(context):
    try:
        await context.bot.get_me()
    except Exception:
        pass


def register(app):
    if LOG:
        try:
            _patch()
            app.add_handler(TypeHandler(Update, _mark), group=-200)
        except Exception as e:
            print("perf log off:", e)
    app.job_queue.run_repeating(_ping, interval=4, first=5)

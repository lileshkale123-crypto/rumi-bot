import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"Rumi is alive"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        port = int(os.environ.get("PORT", "10000"))
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
        except OSError as exc:
            log.warning("Keepalive HTTP server could not bind port %s: %s", port, exc)
            return
        threading.Thread(
            target=srv.serve_forever,
            name="rumi-keepalive",
            daemon=True,
        ).start()
        _started = True

import os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Rumi is alive")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a):
        pass

def start():
    port = int(os.environ.get("PORT", "10000"))
    srv = HTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

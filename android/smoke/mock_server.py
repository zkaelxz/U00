"""Stands in for the Baihe PC while the phone smoke test runs: serves two tiny pages and records every request."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STUDIO_PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Baihe mock</title></head><body><h1>Baihe mock studio</h1><p id="route"></p>
<script>
// The route is reported back so the test can tell the shell opened the page it was asked to.
function report() { fetch('/__seen?hash=' + encodeURIComponent(location.hash)); }
document.getElementById('route').textContent = location.hash;
window.addEventListener('hashchange', report);
report();
</script></body></html>"""

CHAPTER_PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Mock chapter</title></head><body><h1>Mock chapter</h1><p>One paragraph of text.</p></body></html>"""


class MockServer:
    """Threaded HTTP server on all interfaces (the emulator reaches the host as 10.0.2.2) with a request log."""

    def __init__(self, port):
        self.requests = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                with owner._lock:
                    owner.requests.append((self.path, self.headers.get("User-Agent", "")))
                path = self.path.split("?")[0]
                if path == "/":
                    self._send(200, "text/html; charset=utf-8", STUDIO_PAGE)
                elif path == "/chapter":
                    self._send(200, "text/html; charset=utf-8", CHAPTER_PAGE)
                elif path == "/__seen":
                    self._send(204, "text/plain", b"")
                else:
                    # An empty JSON body is what an update check or a feed poll gets from a server with nothing to say.
                    self._send(404, "application/json", json.dumps({"detail": "Not found"}).encode())

            do_POST = do_GET  # noqa: N815

            def _send(self, status, kind, body):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._httpd.shutdown()

    def seen(self, needle, agent=None, exact=False):
        """True once a request whose path contains `needle` (is `needle`, when exact) and whose user agent contains `agent` has arrived."""
        with self._lock:
            return any((path == needle if exact else needle in path) and (agent is None or agent in ua)
                       for path, ua in self.requests)

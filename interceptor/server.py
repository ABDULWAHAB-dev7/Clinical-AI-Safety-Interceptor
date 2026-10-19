"""Minimal HTTP wrapper. POST /api/check with a JSON request returns the decision.

Binds to 127.0.0.1 only. Standard library only.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import InvalidRequest, intercept, load_reference

MAX_BODY_BYTES = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    formulary, interactions = load_reference()

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/check":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, {"error": "invalid Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._send(413, {"error": "body too large"})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"null")
            result = intercept(request, self.formulary, self.interactions)
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return
        except InvalidRequest as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(200, result)


def serve(port=8001):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Interceptor listening on http://127.0.0.1:{port}")
    server.serve_forever()

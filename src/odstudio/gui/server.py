"""Local web server for the GUI.  Listens on 127.0.0.1 only."""
from __future__ import annotations

import json
import mimetypes
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .app import App, UserError

STATIC = Path(__file__).parent / "static"


def _json_default(o):
    return str(o)


def _clean(o):
    """JSON has no NaN/Infinity; the browser would refuse to parse them."""
    if isinstance(o, float):
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(_clean(obj), default=_json_default).encode(), "application/json")

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def do_GET(self):
        if not self._local():
            return self._send(403, b"forbidden", "text/plain")
        path = self.path.split("?")[0]
        if path == "/api/state":
            return self._json(self.app.state())
        if path == "/api/poll":
            return self._json(self.app.poll())
        name = "index.html" if path == "/" else path.lstrip("/")
        f = (STATIC / name).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") or "javascript" in ctype else ""))

    def do_POST(self):
        # The custom header cannot be sent cross-site without a CORS preflight,
        # which this server never grants: other web pages cannot drive the app.
        if not self._local() or self.headers.get("X-ODStudio") != "1":
            return self._send(403, b"forbidden", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"ok": False, "error": "Bad request"}, 400)
        if req.get("op") == "quit":
            self._json({"ok": True, "result": {}})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        try:
            result = self.app.op(req.get("op", ""), req.get("args") or {})
            self._json({"ok": True, "result": result, "state": self.app.state()})
        except UserError as e:
            self._json({"ok": False, "error": str(e), "state": self.app.state()})
        except TypeError as e:
            self._json({"ok": False, "error": f"Bad arguments: {e}"})
        except Exception as e:  # never take the server down
            traceback.print_exc()
            self._json({"ok": False, "error": f"Internal error: {e}"})


def make_server(app: App, host: str = "127.0.0.1", port: int = 8250) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    last: Optional[OSError] = None
    for p in ([port] if port == 0 else range(port, port + 20)):
        try:
            srv = ThreadingHTTPServer((host, p), handler)
            srv.daemon_threads = True
            return srv
        except OSError as e:
            last = e
    raise last


def run(port: int = 8250, instrument: Optional[str] = None, open_browser: bool = True) -> None:
    app = App(port=instrument)
    srv = make_server(app, port=port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"OD Studio is running at {url}\nClose this window (or press Ctrl-C) to quit.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        app.inst.disconnect()
        srv.server_close()

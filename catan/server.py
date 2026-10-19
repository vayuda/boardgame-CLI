"""`catan serve`: play a saved game in the browser.

The page polls for changes, so moves made elsewhere (an agent running
`catan do --as blue ...`) show up within a moment. Commands from the page
go through the same parser as the CLI, under the save file's lock.
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .api import default_seat, view_state
from .game import Game, IllegalAction
from .store import GameStore

LONG_POLL_SECONDS = 25


class CatanServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store: GameStore, seat: str | None):
        super().__init__(addr, Handler)
        self.store = store
        self.fixed_seat = seat          # --as: the browser always plays this seat
        self._cache: tuple[str | None, Game | None] = (None, None)
        self._cache_lock = threading.Lock()

    @staticmethod
    def page() -> bytes:
        return (resources.files("catan") / "web" / "index.html").read_bytes()

    def game(self) -> tuple[str | None, Game]:
        """The current game, re-read only when the file changed."""
        version = self.store.version()
        with self._cache_lock:
            if self._cache[0] != version or self._cache[1] is None:
                self._cache = (version, self.store.load())
            return self._cache

    def seat_for(self, g: Game, requested: str | None) -> int | None:
        from .cli import parse_player
        token = self.fixed_seat or requested
        if token:
            return parse_player(g, token)
        return default_seat(g)


class Handler(BaseHTTPRequestHandler):
    server: CatanServer

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            self._send(200, self.server.page(), "text/html; charset=utf-8")
            return
        if url.path == "/api/state":
            since = q.get("since", [None])[0]
            deadline = time.monotonic() + LONG_POLL_SECONDS
            while since and self.server.store.version() == since and time.monotonic() < deadline:
                time.sleep(0.2)
            try:
                version, g = self.server.game()
                seat = self.server.seat_for(g, q.get("as", [None])[0])
                state = view_state(g, seat)
            except IllegalAction as e:
                self._json({"error": str(e)}, 400)
                return
            state["version"] = version
            self._json(state)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/command":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            cmd = str(data.get("cmd", "")).strip()
        except (ValueError, json.JSONDecodeError):
            self._json({"ok": False, "output": ["! bad request"]}, 400)
            return
        from .cli import Session
        store = self.server.store
        output: list[str] = []
        with store.lock():
            g = store.load()
            try:
                seat = self.server.seat_for(g, data.get("as"))
            except IllegalAction as e:
                self._json({"ok": False, "output": [f"! {e}"]}, 400)
                return
            s = Session(g, None, color=False, write=output.append)
            s.advance()
            if seat is None:
                ok = False
                output.append("! this page is only watching (no seat)")
            elif not cmd:
                ok = False
            else:
                informational = cmd.split()[0].lower() in ("?", "hint", "hints", "legal", "info",
                                                           "ports", "log", "st", "status", "h",
                                                           "help", "m", "map", "board", "legend")
                if not informational and seat not in g.pending():
                    ok = False
                    output.append("! it's not your move - waiting for "
                                  + ", ".join(g.name(p) for p in g.pending()))
                else:
                    ok = s.run_commands(seat, cmd.split(";"), echo_log=False) == 0
            store.save(g)
        version, g = self.server.game()
        state = view_state(g, seat)
        state["version"] = version
        self._json({"ok": ok, "output": output, "state": state})


def serve(store: GameStore, host: str = "127.0.0.1", port: int = 8765, seat: str | None = None,
          open_browser: bool = True) -> int:
    httpd = CatanServer((host, port), store, seat)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{httpd.server_port}/"
    g = store.load()
    who = httpd.seat_for(g, None)
    print(f"Catan is running at {url}")
    print(f"Game file: {store.path} · you play "
          + (g.name(who) if who is not None else "nobody (spectating)") + " · Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0

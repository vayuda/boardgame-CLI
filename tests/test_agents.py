"""Agent seats, the shared save file, `catan wait`, and the web server API."""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

from catan import bots
from catan.api import view_state
from catan.cli import main
from catan.game import Game
from catan.server import CatanServer
from catan.store import GameStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(list(args))
    return code, buf.getvalue()


def first_move(out: str) -> str:
    """Pick a legal setup move from a '?' listing (settlement or road end)."""
    for line in out.splitlines():
        m = re.match(r"\s+(\d+)[: ]", line)
        if m:
            return m.group(1)
    raise AssertionError("no move found in:\n" + out)


class TwoPlayerTests(unittest.TestCase):
    def test_bot_duels_finish(self):
        for seed in range(10):
            g = Game(["Red", "Blue"], bots=[True, True], seed=seed)
            bots.run_bots(g)
            self.assertEqual(g.phase, "game_over")
            self.assertEqual(g.setup_queue, [g.first, 1 - g.first, 1 - g.first, g.first])

    def test_agent_seat_flag_survives_save(self):
        g = Game(["Red", "Claude"], agents=[False, True], seed=1)
        clone = Game.from_dict(json.loads(json.dumps(g.to_dict())))
        self.assertEqual([p.agent for p in clone.players], [False, True])
        self.assertEqual([p.bot for p in clone.players], [False, False])


class SeatPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "g.json")
        code, _ = run_cli("new", "-f", self.path, "--players", "2", "--humans", "1", "--agents", "1",
                          "--names", "Ann,Claude", "--seed", "5", "--first", "1", "--no-color")
        self.assertEqual(code, 0)

    def tearDown(self):
        self.dir.cleanup()

    def play_setup(self):
        """Both seats place their pieces through `do --as`."""
        for _ in range(8):
            g = Game.load(self.path)
            if g.phase != "setup":
                break
            seat = g.players[g.current].name.lower()
            _, out = run_cli("do", "-f", self.path, "--as", seat, "--no-color", "?")
            code, out = run_cli("do", "-f", self.path, "--as", seat, "--no-color", "--no-board",
                                first_move(out))
            self.assertEqual(code, 0, out)
        self.assertNotEqual(Game.load(self.path).phase, "setup")

    def test_do_as_shows_only_that_seat(self):
        self.play_setup()
        g = Game.load(self.path)
        g.players[0].hand.update(brick=7, ore=4)       # Ann's secret hand
        g.save(self.path)
        _, out = run_cli("show", "-f", self.path, "--as", "claude", "--no-color")
        self.assertIn("Claude (you)", out)
        self.assertNotIn("brick 7", out)              # Ann's hand never appears
        self.assertNotIn("[B7", out)
        _, out = run_cli("do", "-f", self.path, "--as", "claude", "--no-color", "st")
        self.assertNotIn("brick 7", out)
        state = json.loads(run_cli("show", "-f", self.path, "--as", "claude", "--json")[1])
        self.assertIsNone(state["players"][0]["hand"])
        self.assertEqual(state["you"]["seat"], 1)
        self.assertEqual(state["players"][0]["cards"], g.players[0].num_cards)

    def test_agent_games_require_a_seat(self):
        for args in (("show",), ("do", "?")):
            code, out = run_cli(args[0], "-f", self.path, "--no-color", *args[1:])
            self.assertEqual(code, 1)
            self.assertIn("--as", out)
            self.assertNotIn("Your hand", out)

    def test_acting_out_of_turn_is_refused(self):
        g = Game.load(self.path)
        idle = 1 - g.current
        code, out = run_cli("do", "-f", self.path, "--as", g.players[idle].name, "--no-color", "s 20")
        self.assertEqual(code, 1)
        self.assertIn("has nothing to do now", out)
        self.assertIn("Waiting for", out)

    def test_wait_returns_on_your_move_and_times_out_otherwise(self):
        g = Game.load(self.path)
        mover, idle = g.players[g.current].name, g.players[1 - g.current].name
        t0 = time.monotonic()
        code, out = run_cli("wait", "-f", self.path, "--as", idle, "--timeout", "0.6", "--no-color")
        self.assertEqual(code, 2)
        self.assertIn("Still waiting for", out)
        self.assertGreater(time.monotonic() - t0, 0.5)
        code, out = run_cli("wait", "-f", self.path, "--as", mover, "--timeout", "5", "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("Next:", out)

    def test_wait_wakes_up_when_the_other_seat_moves(self):
        g = Game.load(self.path)
        mover, idle = g.players[g.current].name, g.players[1 - g.current].name
        env = dict(os.environ, PYTHONPATH=ROOT)
        proc = subprocess.Popen([sys.executable, "-m", "catan", "wait", "-f", self.path, "--as", idle,
                                 "--timeout", "20", "--no-color", "--no-board"],
                                stdout=subprocess.PIPE, text=True, env=env)
        time.sleep(0.8)
        self.assertIsNone(proc.poll())                   # still blocked
        _, out = run_cli("do", "-f", self.path, "--as", mover, "--no-color", "?")
        run_cli("do", "-f", self.path, "--as", mover, "--no-color", first_move(out))
        _, out = run_cli("do", "-f", self.path, "--as", mover, "--no-color", "?")
        run_cli("do", "-f", self.path, "--as", mover, "--no-color", first_move(out))
        stdout, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, stdout)
        self.assertIn("place a settlement", stdout)


class StoreTests(unittest.TestCase):
    def test_concurrent_writers_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.json")
            store = GameStore(path)
            g = Game(["Red", "Blue"], seed=1)
            g.meta["counter"] = 0
            store.save(g)

            def bump(n):
                for _ in range(n):
                    with store.lock():
                        x = store.load()
                        x.meta["counter"] += 1
                        store.save(x)

            threads = [threading.Thread(target=bump, args=(25,)) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(store.load().meta["counter"], 100)
            v1 = store.version()
            store.save(store.load())
            self.assertNotEqual(v1, store.version())


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "g.json")
        run_cli("new", "-f", self.path, "--players", "2", "--humans", "1", "--agents", "1",
                "--names", "Ann,Claude", "--seed", "8", "--first", "1", "--no-color")
        self.httpd = CatanServer(("127.0.0.1", 0), GameStore(self.path), None)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.read()

    def post(self, cmd):
        req = urllib.request.Request(self.base + "/api/command", data=json.dumps({"cmd": cmd}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def test_page_and_state(self):
        self.assertIn(b"<title>Catan Table</title>", self.get("/"))
        s = json.loads(self.get("/api/state"))
        self.assertEqual(s["seat"], 0)                   # the human seat by default
        self.assertTrue(s["yourMove"])
        self.assertEqual(len(s["legal"]["settlements"]), 54)
        self.assertEqual(s["players"][1]["kind"], "agent")
        self.assertEqual(len(s["geometry"]["edges"]), 72)

    def test_commands_and_long_poll(self):
        s = json.loads(self.get("/api/state"))
        res = self.post("s 9999")
        self.assertFalse(res["ok"])
        self.assertTrue(res["output"][0].startswith("!"))
        spot = s["legal"]["settlements"][10]
        res = self.post(f"s {spot}")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["state"]["setupExpect"], "road")
        version = res["state"]["version"]
        a, b = res["state"]["legal"]["roads"][0]

        # A long poll on the current version returns as soon as the game changes.
        result = {}

        def waiter():
            result["state"] = json.loads(self.get(f"/api/state?since={version}"))

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.5)
        self.assertTrue(t.is_alive())
        self.assertTrue(self.post(f"r {a} {b}")["ok"])
        t.join(10)
        self.assertNotEqual(result["state"]["version"], version)
        self.assertFalse(result["state"]["yourMove"])    # the agent's turn now
        self.assertEqual(result["state"]["pending"], [1])
        res = self.post("e")
        self.assertFalse(res["ok"])
        self.assertIn("not your move", res["output"][0])

    def test_view_hides_other_hands(self):
        g = Game.load(self.path)
        g.players[1].hand["ore"] = 5
        s = view_state(g, 0)
        self.assertNotIn("hand", {k for k, v in s["players"][1].items() if v is not None})
        self.assertEqual(s["players"][1]["cards"], 5)


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import os
import random
import tempfile
import unittest

from catan import bots
from catan.board import EDGES, VERTEX_EDGES, edge_between, hex_label
from catan.cli import Session, cards_arg, main, parse_cards, parse_edges, parse_player
from catan.game import COSTS, Game, IllegalAction, edge_name

NAMES = ["Red", "Blue", "Orange", "White"]


class ParseTests(unittest.TestCase):
    def test_cards(self):
        self.assertEqual(parse_cards(["2b", "w"]), {"brick": 2, "wool": 1})
        self.assertEqual(parse_cards(["bbw"]), {"brick": 2, "wool": 1})
        self.assertEqual(parse_cards(["2", "brick", "sheep"]), {"brick": 2, "wool": 1})
        self.assertEqual(parse_cards(["wood,wheat+ore"]), {"lumber": 1, "grain": 1, "ore": 1})
        self.assertEqual(parse_cards(["3xore"]), {"ore": 3})
        for bad in (["x"], ["2"], ["2bb"]):
            with self.assertRaises(IllegalAction):
                parse_cards(bad)

    def test_edges(self):
        self.assertEqual(parse_edges(["8-13"]), [edge_between(8, 13)])
        self.assertEqual(parse_edges(["8", "13"]), [edge_between(8, 13)])
        self.assertEqual(parse_edges(["8-13-18"]), [edge_between(8, 13), edge_between(13, 18)])
        self.assertEqual(parse_edges(["8", "13", "13", "18"]),
                         [edge_between(8, 13), edge_between(13, 18)])
        self.assertEqual(parse_edges(["8", "13", "18"]),
                         [edge_between(8, 13), edge_between(13, 18)])
        with self.assertRaises(IllegalAction):
            parse_edges(["8", "9"])

    def test_players(self):
        g = Game(NAMES, seed=1)
        self.assertEqual(parse_player(g, "blue"), 1)
        self.assertEqual(parse_player(g, "@Or"), 2)
        self.assertEqual(parse_player(g, "4"), 3)
        with self.assertRaises(IllegalAction):
            parse_player(g, "green")


def cli_command(s: Session, a: int, rng: random.Random) -> str:
    """A command string for human player `a`, derived from bot judgement and
    written in a randomly chosen syntax so the parser gets exercised."""
    g = s.g
    pl = g.players[a]
    ph = g.phase
    if ph == "setup":
        if g.setup_expect == "settlement":
            v = max(g.legal_settlements(a, True), key=lambda v: bots.vertex_value(g, a, v))
            return rng.choice([str(v), f"s {v}", f"b s {v}"])
        e = next(e for e in VERTEX_EDGES[g.setup_vertex] if e not in g.roads)
        x, y = EDGES[e]
        u = y if x == g.setup_vertex else x
        return rng.choice([str(u), f"{x}-{y}", f"r {x} {y}", f"road {u}"])
    if ph == "roll":
        if "knight" in g.playable_devs(a) and rng.random() < 0.5:
            h, v = bots.robber_choice(g, a)
            return f"k {hex_label(h)}" + (f" {g.name(v)}" if v is not None else "")
        return rng.choice(["r", "roll"])
    if ph == "discard":
        return "d " + cards_arg(bots.discard_choice(g, a))
    if ph == "robber":
        h, v = bots.robber_choice(g, a)
        return f"rob {hex_label(h).lower()}" + (f" {g.players[v].color}" if v is not None else "")
    if ph == "trade":
        if g.trade["stage"] == "respond":
            return "y" if bots.would_accept(g, a) else "n"
        return "pick " + g.name(g.trade_acceptors()[0])
    if ph == "road_building":
        roads = g.legal_roads(a)
        return f"r {edge_name(roads[0])}" if roads else "stop"
    # main phase
    turn_actions = s.__dict__.setdefault("_actions", {})
    turn_actions[g.turn] = turn_actions.get(g.turn, 0) + 1
    if turn_actions[g.turn] > 30:
        return "e"
    playable = g.playable_devs(a)
    if "year_of_plenty" in playable and g.bank["ore"] and g.bank["grain"]:
        return "yop o g"
    if "monopoly" in playable:
        return rng.choice(["mono wheat", "p m g"])
    if "road_building" in playable and pl.pieces["road"] >= 2:
        return "rb"
    if pl.has(COSTS["city"]) and g.legal_cities(a) and pl.pieces["city"]:
        v = g.legal_cities(a)[0]
        return rng.choice([f"c {v}", f"build city {v}"])
    spots = g.legal_settlements(a) if pl.pieces["settlement"] else []
    if pl.has(COSTS["settlement"]) and spots:
        return rng.choice([f"s {spots[0]}", f"b settlement {spots[0]}"])
    target = bots.road_target(g, a) if pl.pieces["road"] and pl.pieces["settlement"] else None
    if pl.has(COSTS["road"]) and target and not spots:
        x, y = EDGES[target[1]]
        return rng.choice([f"r {x} {y}", f"rd {x}-{y}", f"b r {x} {y}"])
    if pl.has(COSTS["dev"]) and g.dev_deck:
        return rng.choice(["dev", "buy", "b d"])
    if g.trades_proposed == 0 and pl.hand["wool"] >= 1 and rng.random() < 0.5:
        return "offer w for o"
    for r in ("wool", "brick", "lumber", "grain", "ore"):
        ratio = g.trade_ratio(a, r)
        if pl.hand[r] >= ratio + 1:
            want = min(("ore", "grain", "brick", "lumber", "wool"), key=lambda x: pl.hand[x])
            if want != r and g.bank[want]:
                return rng.choice([f"bank {r[0]} {want[0]}", f"bank {ratio}{r[0]} {want}"])
    return rng.choice(["e", "end"])


class PlayThroughCliTests(unittest.TestCase):
    def play(self, seed, humans):
        rng = random.Random(seed)
        g = Game(NAMES, bots=[i >= humans for i in range(4)], seed=seed)
        out = []
        s = Session(g, None, color=False, write=out.append)
        s.advance()
        for _ in range(5000):
            if g.phase == "game_over":
                break
            a = s.actor()
            cmd = cli_command(s, a, rng)
            if rng.random() < 0.05:
                s.execute(a, rng.choice(["?", "st", "m", "info 12", "info k", "log 3", "ports"]))
            try:
                s.execute(a, cmd)
            except IllegalAction as e:
                self.fail(f"seed {seed}: '{cmd}' rejected in phase {g.phase}: {e}")
            s.new_log(s.viewer(a))
            s.advance()
        self.assertEqual(g.phase, "game_over", f"seed {seed} did not finish")

    def test_single_human_games(self):
        for seed in range(12):
            self.play(seed, humans=1)

    def test_hot_seat_games(self):
        for seed in range(4):
            self.play(100 + seed, humans=4)


class OneShotTests(unittest.TestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(args))
        return code, buf.getvalue()

    def test_new_do_show(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.json")
            code, out = self.run_cli("new", "-f", path, "--seed", "3", "--no-color")
            self.assertEqual(code, 0)
            self.assertIn("place a settlement", out)
            self.assertIn("ROBBER", out)
            code, out = self.run_cli("new", "-f", path, "--seed", "4")
            self.assertEqual(code, 1)                      # refuses to clobber
            g = Game.load(path)
            spot = max(g.legal_settlements(g.current, True), key=lambda v: bots.vertex_value(g, 0, v))
            code, out = self.run_cli("do", "-f", path, "--no-color", f"{spot}")
            self.assertEqual(code, 0, out)
            self.assertIn(f"placed a settlement on {spot}", out)
            code, out = self.run_cli("do", "-f", path, "--no-color", "s 1")
            self.assertEqual(code, 1)
            self.assertIn("!", out)
            code, out = self.run_cli("show", "-f", path, "--no-color", "--no-board")
            self.assertIn("place road", out)

    def test_full_game_via_do(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.json")
            self.run_cli("new", "-f", path, "--seed", "9", "--no-color")
            rng = random.Random(9)
            for _ in range(3000):
                g = Game.load(path)
                if g.phase == "game_over":
                    break
                s = Session(g, None, color=False, write=lambda *_: None)
                a = s.actor()
                code, out = self.run_cli("do", "-f", path, "--no-color", "--no-board",
                                         cli_command(s, a, rng))
                self.assertEqual(code, 0, out)
            self.assertEqual(Game.load(path).phase, "game_over")

    def test_chain_stops_when_turn_moves_on(self):
        g = Game(NAMES, bots=[False, True, True, True], seed=21, first=0)
        out = []
        s = Session(g, None, color=False, write=out.append)
        while g.phase == "setup":
            a = s.actor()
            s.execute(a, cli_command(s, a, random.Random(0)))
            s.advance()
        self.assertEqual((s.actor(), g.phase), (0, "main"))
        g.players[0].hand.update(wool=1, grain=1, ore=1)
        status = s.run_commands(0, ["e", "dev"])
        self.assertEqual(status, 1)
        self.assertTrue(any("not run: dev" in line for line in out))
        self.assertEqual(g.players[0].devs, [])

    def test_offers_off_auto_declines(self):
        g = Game(NAMES, bots=[False, True, True, True], seed=22, first=1)
        g.setup_index = len(g.setup_queue)
        g.phase, g.turn, g.rolled, g.current = "main", 1, True, 1
        g.players[1].hand["brick"] = 1
        g.players[0].hand["ore"] = 1
        g.propose_trade(1, {"brick": 1}, {"ore": 1}, to=0)
        s = Session(g, None, color=False, write=lambda *_: None)
        self.assertEqual(s.actor(), 0)
        g.options["offers"] = False
        s.advance()
        self.assertFalse(any("traded 1 brick to Red" in e["t"] for e in g.log))
        self.assertTrue(any(e["t"].strip() == "Red declines." for e in g.log))

    def test_sim(self):
        code, out = self.run_cli("sim", "-n", "3", "--seed", "1")
        self.assertEqual(code, 0)
        self.assertIn("3 games", out)


if __name__ == "__main__":
    unittest.main()

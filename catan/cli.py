"""Command-line front end.

Two ways to play:
  * `catan` / `catan play` - an interactive prompt (hot-seat humans + bots).
  * `catan new|show|do`    - one-shot commands against a saved game file,
                             handy for scripts and AI agents.
Both share the same command language; see HELP.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from . import bots
from .board import (DESERT, EDGES, HEX_VERTICES, NUM_VERTICES, PIPS, RESOURCES, VERTEX_EDGES,
                    VERTEX_HEXES, edge_between, hex_label, parse_hex)
from .game import COSTS, DEV_NAMES, Game, IllegalAction, edge_name, fmt_cards
from .store import GameStore
from .render import (PLAYER_FG, legend, paint, render_board, render_hex_info, render_ports,
                     render_status, render_vertex_info, short_hand)

DEFAULT_SAVE = "catan_save.json"
DEFAULT_NAMES = ("Red", "Blue", "Orange", "White")

RES_ALIASES = {
    "b": "brick", "brick": "brick", "bricks": "brick", "clay": "brick",
    "l": "lumber", "lumber": "lumber", "wood": "lumber", "tree": "lumber", "trees": "lumber",
    "w": "wool", "wool": "wool", "sheep": "wool",
    "g": "grain", "grain": "grain", "wheat": "grain",
    "o": "ore", "ore": "ore", "stone": "ore", "rock": "ore", "rocks": "ore",
}

HELP = """\
COMMANDS  (case-insensitive; chain with ';', e.g.  r 12 13; s 14; e)
 Your turn
  r                    roll the dice (auto-rolls when you hold no playable card)
  r 12 13  | r 12-13   build a road between intersections 12 and 13
  s 14                 build a settlement on 14
  c 14                 upgrade your settlement on 14 to a city
  dev                  buy a development card
  bank w o [n]         trade wool -> ore with the bank at your best rate (n times)
  offer 2b for o [to blue]   offer a trade to everyone (or one player)
  e                    end your turn
 Development cards (one per turn, any time in your turn, even before rolling)
  k F [blue]           Knight: move the robber to hex F, rob Blue
  rb [12-13 13-14]     Road Building: then place 2 free roads (r A B), 'stop' to quit
  yop o g              Year of Plenty: take 1 ore + 1 grain from the bank
  mono w               Monopoly: take every wool from the other players
 When asked
  rob F [blue]         move the robber after a 7 (victim optional if only one)
  d 2o w               discard cards when a 7 is rolled (here 2 ore + 1 wool)
  y | n                accept / decline a trade offered to you
  pick blue | cancel   choose a partner when several accept / withdraw your offer
 Setup: just type the intersection number (23), then the road's other end (24).
 Info
  m        map            st     status table          ?      legal moves + hints
  info 23  intersection   info F hex details           log [n] recent events
  ports    harbours       legend  map symbols          save [file]   q quit (autosaves)
 Settings: set autoroll on|off (roll for you when you hold no playable card)
           set offers on|off   (off = silently decline the bots' trade offers)
           set privacy on|off  (hot-seat: clear the screen between players)
           set color on|off
 Cards: b=brick l=lumber(wood) w=wool(sheep) g=grain(wheat) o=ore.
        Write them as 2b, bb, '2 brick', 'b l' ... (2b w = 2 brick + 1 wool).
 Players: by name, colour or number (blue, b, 2)."""


# ---------------------------------------------------------------- parsing
def parse_resource(word: str) -> str:
    r = RES_ALIASES.get(word.lower())
    if not r:
        raise IllegalAction(f"unknown resource '{word}' (use b l w g o)")
    return r


def parse_cards(tokens) -> dict:
    """'2b w', 'bbw', '2 brick wool', 'b,l' -> {'brick': 2, 'wool': 1}."""
    out = {r: 0 for r in RESOURCES}
    pending = None
    for tok in tokens:
        for part in re.split(r"[,+]", tok.lower()):
            if not part:
                continue
            if part.isdigit():
                pending = int(part)
                continue
            m = re.fullmatch(r"(\d+)x?([a-z]+)", part)
            if m:
                out[parse_resource(m.group(2))] += int(m.group(1))
                pending = None
                continue
            if part in RES_ALIASES:
                out[RES_ALIASES[part]] += pending or 1
            elif all(ch in "blwgo" for ch in part):
                if pending:
                    raise IllegalAction(f"write '{pending}{part[0]}' style counts per resource")
                for ch in part:
                    out[RES_ALIASES[ch]] += 1
            else:
                raise IllegalAction(f"unknown resource '{part}' (use b l w g o)")
            pending = None
    if pending:
        raise IllegalAction("a count must be followed by a resource")
    return {r: k for r, k in out.items() if k}


def parse_player(g: Game, token: str) -> int:
    t = token.lower().lstrip("@")
    if t.isdigit() and 1 <= int(t) <= g.n:
        return int(t) - 1
    for p, pl in enumerate(g.players):
        if t in (pl.name.lower(), pl.color):
            return p
    matches = [p for p, pl in enumerate(g.players)
               if pl.name.lower().startswith(t) or pl.color.startswith(t)]
    if len(matches) == 1:
        return matches[0]
    raise IllegalAction(f"unknown player '{token}' (players: "
                        + ", ".join(pl.name for pl in g.players) + ")")


def parse_vertex(token: str) -> int:
    if not token.isdigit() or not 0 <= int(token) < NUM_VERTICES:
        raise IllegalAction(f"'{token}' is not an intersection (0-{NUM_VERTICES - 1})")
    return int(token)


def parse_hex_arg(token: str) -> int:
    h = parse_hex(token)
    if h is None:
        raise IllegalAction(f"'{token}' is not a hex (A-S)")
    return h


def _edge(a: int, b: int) -> int:
    e = edge_between(a, b)
    if e is None:
        raise IllegalAction(f"{a} and {b} are not neighbouring intersections")
    return e


def parse_edges(tokens) -> list[int]:
    """'12-13', '12 13', '12-13-14' (a path), '12 13 13 14' (pairs)."""
    if not tokens:
        raise IllegalAction("which road? e.g. r 12 13")
    if any("-" in t for t in tokens):
        edges = []
        for t in tokens:
            nums = [parse_vertex(x) for x in t.split("-") if x]
            if len(nums) < 2:
                raise IllegalAction(f"'{t}' is not a road (use A-B)")
            edges += [_edge(a, b) for a, b in zip(nums, nums[1:])]
        return edges
    nums = [parse_vertex(t) for t in tokens]
    if len(nums) % 2 == 0 and all(edge_between(a, b) is not None
                                  for a, b in zip(nums[::2], nums[1::2])):
        return [_edge(a, b) for a, b in zip(nums[::2], nums[1::2])]
    return [_edge(a, b) for a, b in zip(nums, nums[1:])]


def describe_vertex(g: Game, v: int) -> str:
    parts = []
    pips = 0
    for h in VERTEX_HEXES[v]:
        res, num = g.board.terrain[h], g.board.numbers[h]
        if res == DESERT:
            continue
        pips += PIPS[num]
        parts.append(f"{res} {num}")
    port = g.board.vertex_port.get(v)
    port_s = "" if not port else (" +3:1" if port == "any" else f" +2:1 {port}")
    return f"{v:>2}: {pips:>2} pips ({', '.join(parts) or 'nothing'}){port_s}"


# ---------------------------------------------------------------- session
class Session:
    def __init__(self, game: Game, path: str | None = None, color: bool = True,
                 ascii_only: bool = False, write=None):
        self.g = game
        self.path = path
        self.color = color
        self.ascii = ascii_only
        self.write = write or (lambda s="": print(s))
        self.g.options.setdefault("autoroll", True)
        self.g.options.setdefault("offers", True)

    # -- who is acting / viewing
    def humans(self) -> list[int]:
        return [p for p, pl in enumerate(self.g.players) if not pl.bot]

    def actor(self, as_player: int | None = None) -> int | None:
        pend = [p for p in self.g.pending() if not self.g.players[p].bot]
        if as_player is not None:
            if as_player not in pend:
                waiting = ", ".join(self.g.name(p) for p in self.g.pending()) or "nobody"
                raise IllegalAction(f"{self.g.name(as_player)} has nothing to do now "
                                    f"(waiting for: {waiting})")
            return as_player
        return pend[0] if pend else None

    def viewer(self, actor: int | None = None) -> int | None:
        if actor is not None:
            return actor
        hs = self.humans()
        return hs[0] if len(hs) == 1 else None

    def tag(self, p: int) -> str:
        return player_tag_c(self.g, p, self.color)

    # -- flow
    def advance(self):
        """Run bots (and auto-rolls) until a human decision is needed."""
        g = self.g
        while True:
            bots.run_bots(g)
            a = self.actor()
            if (a is not None and g.phase == "trade" and g.trade["stage"] == "respond"
                    and g.players[g.trade["from"]].bot and not g.options.get("offers", True)):
                g.respond_trade(a, False)
                continue
            if (a is not None and g.phase == "roll" and g.current == a
                    and g.options.get("autoroll", True) and not g.playable_devs(a)):
                g.roll(a)
                continue
            return

    def new_log(self, viewer: int | None) -> list[str]:
        seen = self.g.meta.setdefault("seen", {})
        key = str(viewer)
        start = seen.get(key, 0)
        seen[key] = len(self.g.log)
        return [self.format_log(e, viewer) for e in self.g.log[start:]]

    def format_log(self, entry: dict, viewer: int | None) -> str:
        text = self.g.log_text(entry, viewer)
        if text.startswith("=="):
            return paint(text, "1", self.color)
        if text.startswith("***"):
            return paint(text, "1;33", self.color)
        return "  " + text

    def board_due(self, actor: int | None) -> bool:
        """True the first time `actor` looks at a new turn/placement."""
        g = self.g
        if actor is None or g.current != actor:
            return False
        key = f"{g.turn}:{g.setup_index}"
        shown = g.meta.setdefault("board_shown", {})
        if shown.get(str(actor)) == key:
            return False
        shown[str(actor)] = key
        return True

    def save(self, path: str | None = None):
        path = path or self.path
        if path:
            self.g.save(path)
        return path

    # -- presentation
    def board_text(self) -> str:
        return render_board(self.g, self.color, self.ascii)

    def status_text(self, viewer: int | None) -> str:
        return render_status(self.g, viewer, self.color)

    def prompt(self, a: int) -> str:
        return f"{self.status_line(a)} {self.phase_label(a)}> "

    def status_line(self, a: int) -> str:
        """Name, VP, hand and dev cards (lowercase = bought this turn)."""
        g = self.g
        pl = g.players[a]
        cards = short_hand(pl.hand)
        new = list(pl.new_devs)
        letters = []
        for c in pl.devs:
            ch = "V" if c == "victory_point" else DEV_NAMES[c][0]
            if c in new and c != "victory_point":
                new.remove(c)
                ch = ch.lower()
            letters.append(ch)
        devs = "".join(sorted(letters))
        return f"{self.tag(a)} {g.total_vp(a)}VP [{cards}]" + (f" dev:{devs}" if devs else "")

    def phase_label(self, a: int) -> str:
        g = self.g
        ph = g.phase
        if ph == "setup":
            return f"place {g.setup_expect}"
        if ph == "discard":
            return f"discard {g.discards.get(a, 0)}"
        if ph == "robber":
            return "move robber"
        if ph == "road_building":
            return f"{g.free_roads} free road{'s' if g.free_roads != 1 else ''}"
        if ph == "trade":
            return "respond y/n" if g.trade["stage"] == "respond" else "pick partner"
        return ph

    def next_hint(self, a: int) -> str:
        g = self.g
        ph = g.phase
        name = g.name(a)
        if ph == "setup":
            if g.setup_expect == "settlement":
                return f"{name}: place a settlement - type an intersection number (? = best spots)."
            opts = []
            for e in VERTEX_EDGES[g.setup_vertex]:
                if e not in g.roads:
                    x, y = EDGES[e]
                    opts.append(str(y if x == g.setup_vertex else x))
            return (f"{name}: place a road from {g.setup_vertex} - type the other end "
                    f"({', '.join(opts)}).")
        if ph == "roll":
            cards = ", ".join(DEV_NAMES[c] for c in g.playable_devs(a))
            return f"{name}: roll with r" + (f", or first play a card ({cards})." if cards else ".")
        if ph == "discard":
            return (f"{name}: you hold {g.players[a].num_cards} cards - discard "
                    f"{g.discards[a]} (e.g. d 2o w). ? suggests a discard.")
        if ph == "robber":
            return f"{name}: move the robber - rob <hex> [player]. ? ranks targets."
        if ph == "road_building":
            return f"{name}: place {g.free_roads} free road(s) with r A B, or 'stop'."
        if ph == "trade":
            t = g.trade
            if t["stage"] == "respond":
                return (f"{name}: {g.name(t['from'])} offers you {fmt_cards(t['give'])} "
                        f"for {fmt_cards(t['get'])}. Accept? y / n")
            return (f"{name}: accepted by " + ", ".join(g.name(q) for q in g.trade_acceptors())
                    + " - pick <player> or cancel.")
        if ph == "main":
            return f"{name}: build, trade, play a card, or e to end the turn. ? lists options."
        return ""

    # -- hints
    def hints(self, a: int) -> str:
        g = self.g
        pl = g.players[a]
        ph = g.phase
        out = []
        if ph == "setup":
            if g.setup_expect == "settlement":
                spots = g.legal_settlements(a, setup=True)
                spots.sort(key=lambda v: -bots.vertex_value(g, a, v))
                out.append(f"{len(spots)} legal spots; best for you (bot rating):")
                out += ["  " + describe_vertex(g, v) for v in spots[:10]]
            else:
                out.append("Roads from your new settlement (best first):")
                ranked = []
                for e in VERTEX_EDGES[g.setup_vertex]:
                    if e in g.roads:
                        continue
                    x, y = EDGES[e]
                    u = y if x == g.setup_vertex else x
                    best = max((bots.vertex_value(g, a, w) for w in
                                (EDGES[f][0] if EDGES[f][1] == u else EDGES[f][1]
                                 for f in VERTEX_EDGES[u])
                                if w != g.setup_vertex and g.can_place_settlement(a, w, True) is None),
                               default=0)
                    ranked.append((best, u))
                for best, u in sorted(ranked, reverse=True):
                    out.append(f"  {u}   (best follow-up spot rating {best:.1f})")
            return "\n".join(out)
        if ph == "discard":
            sug = bots.discard_choice(g, a)
            return (f"Discard {g.discards[a]} of: {fmt_cards(pl.hand)}\n"
                    f"Suggested: d {cards_arg(sug)}")
        if ph == "robber":
            out.append("Robber targets (best first):")
            for score, h, victim in bots.robber_ranking(g, a)[:6]:
                victims = g.robber_victims(a, h)
                vs = (" " + g.name(victim).lower()) if victim is not None and len(victims) > 1 else ""
                owners = sorted({g.name(g.buildings[v][0]) for v in HEX_VERTICES[h] if v in g.buildings})
                res, num = g.board.terrain[h], g.board.numbers[h]
                out.append(f"  rob {hex_label(h)}{vs:<8} {res} {num or ''}  "
                           f"buildings: {', '.join(owners) or 'none'}")
            return "\n".join(out)
        if ph == "trade":
            if g.trade["stage"] == "respond":
                sug = "accept" if bots.would_accept(g, a) else "decline"
                return f"y to accept, n to decline (the bots' heuristic would {sug})."
            return "pick <player> to trade with one of them, or cancel."
        if ph == "road_building":
            roads = g.legal_roads(a)
            out.append(f"Free roads left: {g.free_roads}. Legal: " + ", ".join(edge_name(e) for e in roads))
            return "\n".join(out)
        if ph == "roll":
            out.append("r to roll.")
        if ph in ("roll", "main"):
            for c in g.playable_devs(a):
                out.append("  play: " + {
                    "knight": "k <hex> [player]   (Knight)",
                    "road_building": "rb               (Road Building)",
                    "year_of_plenty": "yop <r> <r>      (Year of Plenty)",
                    "monopoly": "mono <r>         (Monopoly)"}[c])
        if ph != "main":
            return "\n".join(out)

        def need(build):
            miss = bots.missing(pl.hand, COSTS[build])
            return "affordable" if not miss else "need " + fmt_cards(miss)

        roads = g.legal_roads(a) if pl.pieces["road"] else []
        target = bots.road_target(g, a) if pl.pieces["road"] else None
        line = f"Road [{need('road')}]: {len(roads)} spots"
        if target:
            v, e, d = target
            line += f"; suggested r {edge_name(e)} (toward {v}, {d} away)"
        out.append(line)
        if roads:
            out.append("  " + " ".join(edge_name(e) for e in roads))
        setts = g.legal_settlements(a) if pl.pieces["settlement"] else []
        setts.sort(key=lambda v: -bots.vertex_value(g, a, v))
        out.append(f"Settlement [{need('settlement')}]: "
                   + ("no connected spot" if not setts else f"{len(setts)} spot(s)"))
        out += ["  s " + describe_vertex(g, v) for v in setts[:6]]
        cities = g.legal_cities(a) if pl.pieces["city"] else []
        out.append(f"City [{need('city')}]: " + (" ".join(f"c {v}" for v in cities) or "no settlement to upgrade"))
        out.append(f"Dev card [{need('dev')}]: {len(g.dev_deck)} left")
        trades = [f"bank {r[0]} ? ({g.trade_ratio(a, r)}:1)" for r in RESOURCES
                  if pl.hand[r] >= g.trade_ratio(a, r)]
        out.append("Bank trades: " + (", ".join(trades) if trades else "none affordable"))
        out.append("e to end the turn.")
        return "\n".join(out)

    # -- command execution
    def execute(self, a: int, line: str) -> list[str]:
        """Run one command for player `a`. Returns informational messages."""
        g = self.g
        raw = line.strip().split()
        toks = [t.lower() for t in raw]
        if not toks:
            return []
        cmd, args = toks[0], toks[1:]

        # Bare numbers: placement during setup / free roads.
        if re.fullmatch(r"\d+(-\d+)*", cmd):
            if g.phase == "setup":
                cmd, args = ("s" if g.setup_expect == "settlement" else "rd"), toks
            elif g.phase == "road_building":
                cmd, args = "rd", toks
            else:
                raise IllegalAction("start with a command, e.g. 's 12' or 'r 12 13' (h = help)")

        if cmd in ("b", "build"):
            if not args:
                raise IllegalAction("build what? r(oad) A B, s(ettlement) V, c(ity) V, d(ev)")
            kind, args = args[0], args[1:]
            cmd = {"r": "rd", "road": "rd", "rd": "rd", "s": "s", "settle": "s", "settlement": "s",
                   "c": "c", "city": "c", "d": "dev", "dev": "dev", "card": "dev"}.get(kind)
            if cmd is None:
                raise IllegalAction(f"can't build '{kind}'")
        if cmd in ("p", "play"):
            if not args:
                raise IllegalAction("play what? k(night), rb, yop, mono")
            kind, args = args[0], args[1:]
            cmd = {"k": "k", "knight": "k", "rb": "rb", "road": "rb", "roads": "rb",
                   "road_building": "rb", "yop": "yop", "yp": "yop", "plenty": "yop", "year": "yop",
                   "m": "mono", "mono": "mono", "monopoly": "mono"}.get(kind)
            if cmd is None:
                raise IllegalAction(f"unknown card '{kind}'")

        # ---- information
        if cmd in ("h", "help"):
            return [HELP]
        if cmd in ("m", "map", "board"):
            return [self.board_text()]
        if cmd in ("st", "status"):
            return [self.status_text(self.viewer(a))]
        if cmd in ("?", "hint", "hints", "legal"):
            return [self.hints(a)]
        if cmd == "ports":
            return [render_ports(g)]
        if cmd == "legend":
            return [legend()]
        if cmd == "log":
            k = int(args[0]) if args and args[0].isdigit() else 20
            return [self.format_log(e, self.viewer(a)) for e in g.log[-k:]]
        if cmd == "info":
            if not args:
                raise IllegalAction("info <intersection> or info <hex>")
            if args[0].isdigit():
                return [render_vertex_info(g, parse_vertex(args[0]), a)]
            return [render_hex_info(g, parse_hex_arg(args[0]))]
        if cmd == "set":
            settings = ("autoroll", "offers", "privacy", "color", "colour")
            if len(args) != 2 or args[0] not in settings or args[1] not in ("on", "off"):
                raise IllegalAction("set autoroll|offers|privacy|color on|off")
            on = args[1] == "on"
            if args[0] in ("color", "colour"):
                self.color = on
            else:
                g.options[args[0]] = on
            return [f"{args[0]} {'on' if on else 'off'}"]
        if cmd == "save":
            path = self.save(raw[1] if len(raw) > 1 else None)
            return [f"saved to {path}" if path else "no file given: save <file>"]

        # ---- actions
        if cmd in ("r", "roll") and not args:
            g.roll(a)
        elif cmd in ("r", "rd", "road"):
            edges = self._road_args(a, args)
            for e in edges:
                if g.phase == "setup":
                    g.place_road_setup(a, e)
                else:
                    g.build_road(a, e)
        elif cmd in ("s", "settle", "settlement"):
            v = parse_vertex(args[0]) if len(args) == 1 else self._one_arg_error("s <intersection>")
            if g.phase == "setup":
                g.place_settlement_setup(a, v)
            else:
                g.build_settlement(a, v)
        elif cmd in ("c", "city"):
            v = parse_vertex(args[0]) if len(args) == 1 else self._one_arg_error("c <intersection>")
            g.build_city(a, v)
        elif cmd in ("dev", "buy"):
            g.buy_dev(a)
        elif cmd in ("k", "knight"):
            h = parse_hex_arg(args[0]) if args else None
            victim = parse_player(g, args[1]) if len(args) > 1 else None
            g.play_knight(a, h, victim)
        elif cmd == "rb":
            edges = parse_edges(args) if args else []
            g.play_road_building(a)
            for e in edges:
                if g.phase == "road_building":
                    g.build_road(a, e)
        elif cmd == "stop":
            g.end_road_building(a)
        elif cmd in ("yop", "plenty"):
            cards = parse_cards(args)
            picks = [r for r, k in cards.items() for _ in range(k)]
            if len(picks) != 2:
                raise IllegalAction("name two resources, e.g. yop o g")
            g.play_year_of_plenty(a, picks[0], picks[1])
        elif cmd in ("mono", "monopoly"):
            if len(args) != 1:
                raise IllegalAction("mono <resource>")
            g.play_monopoly(a, parse_resource(args[0]))
        elif cmd in ("rob", "robber"):
            if not args:
                raise IllegalAction("rob <hex> [player]  (? ranks targets)")
            h = parse_hex_arg(args[0])
            victim = parse_player(g, args[1]) if len(args) > 1 else None
            g.move_robber(a, h, victim)
        elif cmd in ("d", "discard"):
            g.discard(a, parse_cards(args))
        elif cmd in ("bank", "mt", "tb"):
            self._bank(a, args)
        elif cmd in ("offer", "o", "trade", "t"):
            self._offer(a, args)
        elif cmd in ("y", "yes", "accept", "pick", "with"):
            if g.phase != "trade":
                raise IllegalAction("there is no trade offer to answer")
            if g.trade["stage"] == "choose" or cmd in ("pick", "with"):
                if not args:
                    raise IllegalAction("pick which player? " + ", ".join(
                        g.name(q) for q in g.trade_acceptors()))
                g.confirm_trade(a, parse_player(g, args[0]))
            else:
                g.respond_trade(a, True)
        elif cmd in ("n", "no", "decline", "reject"):
            if g.phase != "trade":
                raise IllegalAction("there is no trade offer to answer")
            if g.trade["stage"] == "choose":
                g.cancel_trade(a)
            else:
                g.respond_trade(a, False)
        elif cmd in ("cancel", "withdraw"):
            g.cancel_trade(a)
        elif cmd in ("e", "end", "done", "pass"):
            if g.phase == "road_building":
                raise IllegalAction("place your free roads first (or 'stop')")
            g.end_turn(a)
        else:
            raise IllegalAction(f"unknown command '{raw[0]}' (h = help)")
        return []

    def run_commands(self, a: int, commands, echo_log: bool = True) -> int:
        """Run a ';'-chain of commands for `a`, letting bots respond in between.
        Stops at the first error, or once the game has moved past `a`'s
        moment (turn ended, or other players placed pieces). Returns 0/1."""
        g = self.g
        commands = [c for c in commands if c.strip()]
        for i, cmd in enumerate(commands):
            turn, setup_index = g.turn, g.setup_index
            try:
                messages = self.execute(a, cmd)
            except IllegalAction as e:
                self.write(paint(f"! {e}", "1;31", self.color))
                return 1
            self.advance()
            for line in (self.new_log(self.viewer(a)) if echo_log else []) + messages:
                self.write(line)
            moved_on = (self.actor() != a or g.turn != turn or g.setup_index - setup_index > 1)
            if moved_on and i + 1 < len(commands):
                self.write(paint("! the game moved on; not run: " + "; ".join(commands[i + 1:]),
                                 "1;31", self.color))
                return 1
        return 0

    def _one_arg_error(self, usage: str):
        raise IllegalAction(f"usage: {usage}")

    def _road_args(self, a: int, args) -> list[int]:
        g = self.g
        if len(args) == 1 and args[0].isdigit():
            u = parse_vertex(args[0])
            if g.phase == "setup":
                return [_edge(g.setup_vertex, u)]
            options = [e for e in VERTEX_EDGES[u] if g.can_place_road(a, e) is None]
            if len(options) == 1:
                return options
            if not options:
                raise IllegalAction(f"no road you can build touches {u}")
            raise IllegalAction(f"which road? " + ", ".join(edge_name(e) for e in options))
        return parse_edges(args)

    def _bank(self, a: int, args):
        g = self.g
        args = [t for t in args if t not in ("for", "->", "=", "to", "x")]
        times = None
        if args and args[-1].isdigit():
            times = int(args.pop())
        if len(args) != 2:
            raise IllegalAction("usage: bank <give> <get> [times], e.g. bank w o  or  bank 4w o")
        give, get = parse_cards([args[0]]), parse_cards([args[1]])
        if len(give) != 1 or len(get) != 1:
            raise IllegalAction("bank trades are one resource for another")
        (gr, gk), (wr, wk) = next(iter(give.items())), next(iter(get.items()))
        ratio = g.trade_ratio(a, gr)
        if times is None:
            if wk > 1:
                times = wk
            elif gk > 1:
                if gk % ratio:
                    raise IllegalAction(f"your rate for {gr} is {ratio}:1")
                times = gk // ratio
            else:
                times = 1
        if gk > 1 and gk != ratio * times:
            raise IllegalAction(f"your rate for {gr} is {ratio}:1 ({ratio * times} {gr} "
                                f"for {times} {wr})")
        g.maritime_trade(a, gr, wr, times)

    def _offer(self, a: int, args):
        g = self.g
        to = None
        rest = []
        i = 0
        while i < len(args):
            t = args[i]
            if t == "to" and i + 1 < len(args):
                to = parse_player(g, args[i + 1])
                i += 2
                continue
            if t.startswith("@"):
                to = parse_player(g, t)
            else:
                rest.append(t)
            i += 1
        seps = [k for k, t in enumerate(rest) if t in ("for", "->", "=", "get", "|")]
        if len(seps) != 1:
            raise IllegalAction("usage: offer <cards> for <cards> [to player], e.g. offer 2b for o")
        k = seps[0]
        g.propose_trade(a, parse_cards(rest[:k]), parse_cards(rest[k + 1:]), to)


def player_tag_c(g: Game, p: int, color: bool) -> str:
    return paint(g.players[p].name, PLAYER_FG.get(g.players[p].color), color)


def cards_arg(cards: dict) -> str:
    return " ".join(f"{k}{r[0]}" if k > 1 else r[0] for r, k in cards.items() if k)


# ---------------------------------------------------------------- front ends
def new_game_from_args(args) -> Game:
    n = args.players
    humans = max(0, min(n, args.humans))
    agents = max(0, min(n - humans, getattr(args, "agents", 0) or 0))
    names = [x.strip() for x in args.names.split(",")] if args.names else []
    names = (names + [nm for nm in DEFAULT_NAMES if nm not in names])[:n]
    first = None
    if args.first:
        first = int(args.first) - 1
    return Game(names, bots=[i >= humans + agents for i in range(n)],
                agents=[humans <= i < humans + agents for i in range(n)],
                seed=args.seed, layout=args.layout, first=first)


def use_color(args) -> bool:
    if getattr(args, "no_color", False) or os.environ.get("NO_COLOR"):
        return False
    if getattr(args, "color", False):
        return True
    return sys.stdout.isatty()


def repl(session: Session):
    g = session.g
    w = session.write
    if any(pl.agent for pl in g.players):
        w("This game has agent seats, which play through the save file. "
          "Play your seat in the browser with `catan serve` (or with `catan do --as <you>`).")
        return
    w(paint("Settlers of Catan", "1", session.color) + " - type h for help, ? for hints, q to quit.")
    if session.path:
        w(f"(autosaving to {session.path})")
    session.advance()
    while True:
        a = session.actor()
        viewer = session.viewer(a)
        for line in session.new_log(viewer):
            w(line)
        if g.phase == "game_over":
            w(session.board_text())
            w(session.status_text(None))
            session.save()
            return
        if a is None:
            w("No human players: use 'catan sim' to watch bots.")
            return
        last = getattr(session, "_last_actor", None)
        if last is not None and last != a and len(session.humans()) > 1:
            if g.options.get("privacy", True) and sys.stdin.isatty():
                w("\x1b[2J\x1b[H" if session.color else "\n" * 60)
                try:
                    input(f"Pass to {g.name(a)} and press Enter... ")
                except (EOFError, KeyboardInterrupt):
                    w("")
                    return
                g.meta.setdefault("board_shown", {}).pop(str(a), None)
            w(paint(f"===== {g.name(a)} =====", "1", session.color))
        session._last_actor = a
        if session.board_due(a):
            w(session.board_text())
            w(session.status_text(a))
        hint_key = (a, g.phase, g.turn, g.setup_index, g.setup_expect, g.free_roads,
                    g.trade["stage"] if g.trade else None)
        if hint_key != getattr(session, "_hint_key", None):
            session._hint_key = hint_key
            w(paint(session.next_hint(a), "36", session.color))
        try:
            line = input(session.prompt(a))
        except (EOFError, KeyboardInterrupt):
            w("")
            path = session.save()
            w(f"Game saved to {path}." if path else "Bye.")
            return
        if line.strip().lower() in ("q", "quit", "exit"):
            path = session.save()
            how = "catan play --resume" if path == DEFAULT_SAVE else f"catan play --load {path}"
            w(f"Game saved to {path}; resume with: {how}" if path else "Bye.")
            return
        session.run_commands(a, line.split(";"))
        session.save()


def oneshot_report(session: Session, a: int | None, show_board: bool, quiet: bool = False,
                   viewer: int | None = None, seat_view: bool = False):
    """Print what happened since this viewer last looked, then what's next.
    With seat_view, everything is shown strictly from `viewer`'s seat."""
    g = session.g
    w = session.write
    if not seat_view:
        viewer = session.viewer(a)
    for line in session.new_log(viewer):
        w(line)
    due = session.board_due(a)
    if show_board or (due and not quiet):
        w(session.board_text())
        w(session.status_text(viewer))
    if g.phase == "game_over":
        w(session.status_text(None))
        return
    if a is None:
        if viewer is not None:
            w(session.status_line(viewer))
        w("Waiting for: " + ", ".join(g.name(p) for p in g.pending()))
        return
    w(session.status_line(a) + " " + session.phase_label(a))
    w("Next: " + session.next_hint(a))


def cmd_play(args):
    path = None if args.no_save else (args.save or DEFAULT_SAVE)
    color = use_color(args)
    game = None
    load = args.load or (path if args.resume else None)
    if load:
        game = Game.load(load)
    elif path and os.path.exists(path) and sys.stdin.isatty():
        try:
            old = Game.load(path)
            if old.phase != "game_over" and not any(pl.agent for pl in old.players):
                ans = input(f"Resume the saved game in {path} (turn {old.turn})? [Y/n] ")
                if ans.strip().lower() in ("", "y", "yes"):
                    game = old
        except Exception:
            pass
    if game is None:
        game = new_game_from_args(args)
    if args.web:
        from .server import serve
        store = GameStore(path or DEFAULT_SAVE)
        with store.lock():
            Session(game, None, color=False, write=lambda *_: None).advance()
            store.save(game)
        return serve(store, port=args.port, open_browser=not args.no_open)
    session = Session(game, path, color, args.ascii)
    repl(session)


def cmd_new(args):
    store = GameStore(args.file)
    with store.lock():
        if store.exists() and not args.force:
            try:
                old = store.load()
                if old.phase != "game_over":
                    print(f"{args.file} holds an unfinished game (turn {old.turn}); "
                          "use --force to replace it.")
                    return 1
            except Exception:
                pass
        game = new_game_from_args(args)
        session = Session(game, None, use_color(args), args.ascii)
        session.advance()
        seat = parse_player(game, args.as_player) if getattr(args, "as_player", None) else None
        _report_for(session, seat, show_board=not args.no_board, quiet=args.no_board)
        store.save(game)
    return 0


def _open_store(args) -> GameStore:
    store = GameStore(args.file)
    if not store.exists():
        raise SystemExit(f"no game in {args.file}; start one with: catan new")
    return store


def _require_seat(g: Game, args):
    """Games with agent seats are shared by several players, so every view must
    name its seat; otherwise one player could see another's hand."""
    if not args.as_player and any(pl.agent for pl in g.players):
        raise IllegalAction("this game has agent seats: say whose view you want with --as <seat> "
                            "(" + ", ".join(pl.color for pl in g.players) + ")")


def _report_for(s: Session, seat: int | None, show_board: bool, quiet: bool):
    """Report from one seat's point of view, or (no seat) the first human's."""
    if seat is None:
        oneshot_report(s, s.actor(), show_board, quiet)
        return
    a = seat if seat in s.g.pending() else None
    oneshot_report(s, a, show_board, quiet, viewer=seat, seat_view=True)


def cmd_show(args):
    store = _open_store(args)
    with store.lock():
        s = Session(store.load(), None, use_color(args), args.ascii)
        _require_seat(s.g, args)
        s.advance()
        seat = parse_player(s.g, args.as_player) if args.as_player else None
        if args.json:
            from .api import view_state
            print(json.dumps(view_state(s.g, seat)))
            store.save(s.g)
            return 0
        if args.log:
            viewer = seat if seat is not None else s.viewer(s.actor())
            for e in s.g.log[-args.log:]:
                s.write(s.format_log(e, viewer))
        _report_for(s, seat, show_board=not args.no_board, quiet=False)
        store.save(s.g)
    return 0


def cmd_do(args):
    store = _open_store(args)
    commands = [c for chunk in args.commands for c in chunk.split(";") if c.strip()]
    with store.lock():
        s = Session(store.load(), None, use_color(args), args.ascii)
        g = s.g
        _require_seat(g, args)
        s.advance()
        seat = parse_player(g, args.as_player) if args.as_player else None
        status = 1
        try:
            a = s.actor(seat)
        except IllegalAction as e:
            s.write(f"! {e}")
        else:
            if a is None:
                s.write("! no human move is pending" + (" (game over)" if g.phase == "game_over" else ""))
            else:
                if seat is None and len(s.humans()) > 1:
                    seat = a  # several seats share this file: report only from the one that moved
                status = s.run_commands(a, commands)
        _report_for(s, seat, show_board=args.board, quiet=args.no_board)
        store.save(g)
    return status


def cmd_wait(args):
    """Block until the given seat has a move to make (or the game ends)."""
    store = _open_store(args)
    deadline = time.monotonic() + args.timeout
    seen_version = None
    seat = None
    while True:
        version = store.version()
        if version != seen_version:
            seen_version = version
            g = store.load()
            if seat is None:
                seat = parse_player(g, args.as_player)
            if g.phase == "game_over" or seat in g.pending():
                with store.lock():
                    s = Session(store.load(), None, use_color(args), args.ascii)
                    s.advance()
                    g = s.g
                    if g.phase == "game_over" or seat in g.pending():
                        _report_for(s, seat, show_board=args.board, quiet=args.no_board)
                        store.save(g)
                        return 3 if g.phase == "game_over" else 0
        if time.monotonic() >= deadline:
            g = store.load()
            print(f"Still waiting for: {', '.join(g.name(p) for p in g.pending())} "
                  f"(gave up after {args.timeout:g}s; run wait again)")
            return 2
        time.sleep(args.poll)


def cmd_serve(args):
    from .server import serve
    store = GameStore(args.file)
    if not store.exists():
        raise SystemExit(f"no game in {args.file}; start one first, e.g.\n"
                         "  catan new --players 2 --humans 1 --agents 1 --names You,Claude")
    return serve(store, host=args.host, port=args.port, seat=args.as_player,
                 open_browser=not args.no_open)


def cmd_sim(args):
    import collections
    wins = collections.Counter()
    turns = []
    for i in range(args.games):
        seed = None if args.seed is None else args.seed + i
        g = Game(list(DEFAULT_NAMES[:args.players]), bots=[True] * args.players, seed=seed,
                 layout=args.layout)
        bots.run_bots(g)
        if args.verbose:
            for e in g.log:
                print(g.log_text(e, None))
            print(render_board(g, use_color(args), args.ascii))
            print(render_status(g, None, use_color(args)))
        if g.winner is not None:
            wins[g.players[g.winner].name] += 1
            turns.append(g.turn)
    if turns:
        print(f"{len(turns)} games, average {sum(turns) / len(turns):.1f} turns "
              f"(min {min(turns)}, max {max(turns)}); wins: "
              + ", ".join(f"{k} {v}" for k, v in wins.most_common()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-color", action="store_true", help="plain text output")
    common.add_argument("--color", action="store_true", help="force ANSI colour")
    common.add_argument("--ascii", action="store_true", help="ASCII-only symbols")

    newopts = argparse.ArgumentParser(add_help=False)
    newopts.add_argument("--players", type=int, default=4, choices=(2, 3, 4))
    newopts.add_argument("--humans", type=int, default=1,
                         help="number of human (hot-seat) players; they take the first seats")
    newopts.add_argument("--agents", type=int, default=0,
                         help="seats after the humans played by outside agents via "
                              "`catan wait/do --as`; remaining seats are bots")
    newopts.add_argument("--names", help="comma-separated player names (humans first)")
    newopts.add_argument("--seed", type=int, help="random seed (reproducible games)")
    newopts.add_argument("--layout", choices=("random", "beginner"), default="random")
    newopts.add_argument("--first", help="player number who starts (default: random)")

    fileopt = argparse.ArgumentParser(add_help=False)
    fileopt.add_argument("--file", "-f", default=DEFAULT_SAVE, help=f"game file (default {DEFAULT_SAVE})")
    fileopt.add_argument("--as", dest="as_player", help="act/view as this player (hot-seat)")

    ap = argparse.ArgumentParser(prog="catan", description="Settlers of Catan in your terminal.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("play", parents=[common, newopts], help="interactive game (default)")
    p.add_argument("--save", help=f"autosave file (default {DEFAULT_SAVE})")
    p.add_argument("--no-save", action="store_true", help="don't autosave")
    p.add_argument("--resume", action="store_true", help="resume the autosaved game")
    p.add_argument("--load", help="resume a game from this file")
    p.add_argument("--web", action="store_true", help="play in the browser instead of the terminal")
    p.add_argument("--port", type=int, default=8765, help="port for --web")
    p.add_argument("--no-open", action="store_true", help="with --web: don't open a browser tab")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("new", parents=[common, newopts], help="start a game in a file (one-shot mode)")
    p.add_argument("--file", "-f", default=DEFAULT_SAVE)
    p.add_argument("--force", action="store_true", help="replace an unfinished game")
    p.add_argument("--as", dest="as_player", help="print the opening view for this seat")
    p.add_argument("--no-board", action="store_true", help="don't print the board")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("show", parents=[common, fileopt], help="print board, status and what's next")
    p.add_argument("--log", type=int, default=0, help="also print the last N log lines")
    p.add_argument("--no-board", action="store_true")
    p.add_argument("--json", action="store_true", help="machine-readable state for this seat")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("do", parents=[common, fileopt], help="run command(s) on the saved game")
    p.add_argument("commands", nargs="+", help="e.g. 'r 12 13; s 14; e'")
    p.add_argument("--board", action="store_true", help="always print the board afterwards")
    p.add_argument("--no-board", action="store_true", help="never print the board")
    p.set_defaults(func=cmd_do)

    p = sub.add_parser("wait", parents=[common, fileopt],
                       help="block until a seat has a move to make (for agents)")
    p.add_argument("--timeout", type=float, default=540,
                   help="give up after this many seconds (exit 2); default 540")
    p.add_argument("--poll", type=float, default=0.5, help=argparse.SUPPRESS)
    p.add_argument("--board", action="store_true", help="always print the board")
    p.add_argument("--no-board", action="store_true", help="never print the board")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("serve", parents=[fileopt], help="play in your browser (local web UI)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("sim", parents=[common], help="bot-only games (testing / watching)")
    p.add_argument("-n", "--games", type=int, default=1)
    p.add_argument("--players", type=int, default=4, choices=(2, 3, 4))
    p.add_argument("--seed", type=int)
    p.add_argument("--layout", choices=("random", "beginner"), default="random")
    p.add_argument("-v", "--verbose", action="store_true", help="print each game's log and board")
    p.set_defaults(func=cmd_sim)
    return ap


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv.insert(0, "play")
    args = build_parser().parse_args(argv)
    if args.cmd == "wait" and not args.as_player:
        print("! wait needs --as <seat>, e.g. catan wait --as blue")
        return 1
    try:
        return args.func(args) or 0
    except IllegalAction as e:
        print(f"! {e}")
        return 1
    except BrokenPipeError:
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())

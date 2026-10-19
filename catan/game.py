"""Rules engine for the classic Catan base game (3-4 players).

The engine is a pure state machine: every move is a method call that
either changes the state or raises IllegalAction.  It never prompts.
`pending()` reports which players must act next, so any front end
(interactive REPL, one-shot CLI, bots, tests) can drive it.
"""

from __future__ import annotations

import json
import random
from collections import Counter

from .board import (DESERT, EDGES, HEX_VERTICES, NUM_EDGES, NUM_HEXES, NUM_VERTICES,
                    PIPS, RESOURCES, VERTEX_EDGES, VERTEX_HEXES, VERTEX_NEIGHBORS, Board,
                    hex_label)

COSTS = {
    "road": {"brick": 1, "lumber": 1},
    "settlement": {"brick": 1, "lumber": 1, "wool": 1, "grain": 1},
    "city": {"grain": 2, "ore": 3},
    "dev": {"wool": 1, "grain": 1, "ore": 1},
}
PIECES = {"road": 15, "settlement": 5, "city": 4}
BANK_PER_RESOURCE = 19
DEV_COUNTS = {"knight": 14, "road_building": 2, "year_of_plenty": 2,
              "monopoly": 2, "victory_point": 5}
DEV_NAMES = {"knight": "Knight", "road_building": "Road Building",
             "year_of_plenty": "Year of Plenty", "monopoly": "Monopoly",
             "victory_point": "Victory Point"}
PLAYER_COLORS = ("red", "blue", "orange", "white")
VP_TO_WIN = 10
LONGEST_ROAD_MIN = 5
LARGEST_ARMY_MIN = 3

PHASES = ("setup", "roll", "discard", "robber", "main", "road_building", "trade", "game_over")


class IllegalAction(Exception):
    """Raised when a move is not allowed; the message explains why."""


def fmt_cards(cards: dict) -> str:
    parts = [f"{n} {r}" for r in RESOURCES if (n := cards.get(r, 0))]
    return ", ".join(parts) if parts else "nothing"


def edge_name(e: int) -> str:
    a, b = EDGES[e]
    return f"{a}-{b}"


class Player:
    def __init__(self, name: str, color: str, bot: bool):
        self.name = name
        self.color = color
        self.bot = bot
        self.hand = {r: 0 for r in RESOURCES}
        self.devs: list[str] = []       # unplayed cards, including VP cards
        self.new_devs: list[str] = []   # bought this turn (not yet playable)
        self.knights = 0
        self.pieces = dict(PIECES)

    @property
    def num_cards(self) -> int:
        return sum(self.hand.values())

    def has(self, cards: dict) -> bool:
        return all(self.hand.get(r, 0) >= n for r, n in cards.items())

    def to_dict(self) -> dict:
        return dict(name=self.name, color=self.color, bot=self.bot, hand=self.hand,
                    devs=self.devs, new_devs=self.new_devs, knights=self.knights,
                    pieces=self.pieces)

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        p = cls(d["name"], d["color"], d["bot"])
        p.hand = {r: d["hand"].get(r, 0) for r in RESOURCES}
        p.devs = list(d["devs"])
        p.new_devs = list(d["new_devs"])
        p.knights = d["knights"]
        p.pieces = dict(d["pieces"])
        return p


class Game:
    def __init__(self, names, bots=None, seed=None, layout="random", first=None):
        n = len(names)
        if n not in (3, 4):
            raise ValueError("the base game is for 3 or 4 players")
        bots = list(bots) if bots is not None else [False] * n
        self.seed = seed if seed is not None else random.randrange(10**9)
        self.rng = random.Random(self.seed)
        self.layout = layout
        self.board = Board.generate(layout, self.rng)
        self.players = [Player(nm, PLAYER_COLORS[i], bots[i]) for i, nm in enumerate(names)]
        self.bank = {r: BANK_PER_RESOURCE for r in RESOURCES}
        self.dev_deck = [c for c, k in DEV_COUNTS.items() for _ in range(k)]
        self.rng.shuffle(self.dev_deck)
        self.buildings: dict[int, tuple[int, str]] = {}   # vertex -> (player, kind)
        self.roads: dict[int, int] = {}                    # edge -> player
        self.robber = self.board.desert
        self.first = self.rng.randrange(n) if first is None else first
        self.current = self.first
        order = [(self.first + i) % n for i in range(n)]
        self.setup_queue = order + order[::-1]
        self.setup_index = 0
        self.setup_expect = "settlement"
        self.setup_vertex = None
        self.phase = "setup"
        self.turn = 0
        self.rolled = False
        self.dev_played = False
        self.last_roll = None
        self.discards: dict[int, int] = {}
        self.free_roads = 0
        self.return_phase = None
        self.trade = None
        self.trades_proposed = 0
        self.longest_road = None
        self.largest_army = None
        self.winner = None
        self.log: list[dict] = []
        self.options: dict = {}
        self.meta: dict = {}
        self._log(f"New game (seed {self.seed}, {layout} board). "
                  f"{self.players[self.first].name} goes first.")
        self._log(f"Setup: {self.players[self.current].name} places a settlement and road.")

    # ------------------------------------------------------------------ logging
    def _log(self, public: str, private: str | None = None, to=None):
        entry = {"t": public}
        if private is not None:
            entry["p"] = private
            entry["to"] = sorted(set(to))
        self.log.append(entry)

    def log_text(self, entry: dict, viewer: int | None) -> str:
        """Text of a log entry as seen by `viewer` (None = sees everything)."""
        if "p" in entry and (viewer is None or viewer in entry["to"]):
            return entry["p"]
        return entry["t"]

    def name(self, p: int) -> str:
        return self.players[p].name

    # ------------------------------------------------------------------ queries
    @property
    def n(self) -> int:
        return len(self.players)

    def pending(self) -> list[int]:
        """Players who are expected to act now, in a sensible order."""
        if self.phase == "game_over":
            return []
        if self.phase == "discard":
            return sorted(self.discards, key=lambda p: (p - self.current) % self.n)
        if self.phase == "trade":
            t = self.trade
            if t["stage"] == "respond":
                return [q for q in t["targets"] if str(q) not in t["responses"]]
            return [t["from"]]
        return [self.current]

    def public_vp(self, p: int) -> int:
        vp = 0
        for owner, kind in self.buildings.values():
            if owner == p:
                vp += 2 if kind == "city" else 1
        if self.longest_road == p:
            vp += 2
        if self.largest_army == p:
            vp += 2
        return vp

    def total_vp(self, p: int) -> int:
        return self.public_vp(p) + self.players[p].devs.count("victory_point")

    def trade_ratio(self, p: int, res: str) -> int:
        ratio = 4
        for v, kind in self.board.vertex_port.items():
            b = self.buildings.get(v)
            if b and b[0] == p:
                if kind == res:
                    return 2
                if kind == "any":
                    ratio = 3
        return ratio

    def player_ports(self, p: int) -> list[str]:
        kinds = set()
        for v, kind in self.board.vertex_port.items():
            b = self.buildings.get(v)
            if b and b[0] == p:
                kinds.add(kind)
        return sorted(kinds, key=lambda k: (k != "any", k))

    def production(self, p: int) -> dict:
        """Expected pips per resource for player p (robber ignored)."""
        out = {r: 0 for r in RESOURCES}
        for v, (owner, kind) in self.buildings.items():
            if owner != p:
                continue
            for h in VERTEX_HEXES[v]:
                res = self.board.terrain[h]
                if res != DESERT:
                    out[res] += PIPS[self.board.numbers[h]] * (2 if kind == "city" else 1)
        return out

    def _blocked_for(self, p: int, v: int) -> bool:
        b = self.buildings.get(v)
        return b is not None and b[0] != p

    def road_length(self, p: int) -> int:
        """Longest continuous road of player p, broken by opponents' buildings."""
        adj: dict[int, list[tuple[int, int]]] = {}
        for e, owner in self.roads.items():
            if owner == p:
                a, b = EDGES[e]
                adj.setdefault(a, []).append((e, b))
                adj.setdefault(b, []).append((e, a))
        best = 0
        used: set[int] = set()

        def dfs(v: int, length: int):
            nonlocal best
            if length > best:
                best = length
            if length and self._blocked_for(p, v):
                return
            for e, u in adj.get(v, ()):
                if e not in used:
                    used.add(e)
                    dfs(u, length + 1)
                    used.discard(e)

        for v in adj:
            dfs(v, 0)
        return best

    def can_place_settlement(self, p: int, v: int, setup: bool = False) -> str | None:
        """None if p may build a settlement on v, else the reason."""
        if not 0 <= v < NUM_VERTICES:
            return f"there is no intersection {v}"
        if v in self.buildings:
            return f"intersection {v} is occupied"
        for u in VERTEX_NEIGHBORS[v]:
            if u in self.buildings:
                return f"too close to the building on {u} (distance rule)"
        if not setup and not any(self.roads.get(e) == p for e in VERTEX_EDGES[v]):
            return f"intersection {v} is not connected to one of your roads"
        return None

    def can_place_road(self, p: int, e: int) -> str | None:
        if not 0 <= e < NUM_EDGES:
            return "no such path"
        if e in self.roads:
            return f"path {edge_name(e)} already has a road"
        if self.phase == "setup":
            if self.setup_vertex not in EDGES[e]:
                return f"the road must touch your new settlement on {self.setup_vertex}"
            return None
        for v in EDGES[e]:
            b = self.buildings.get(v)
            if b is not None:
                if b[0] == p:
                    return None
                continue  # cannot build through an opponent's building
            if any(self.roads.get(f) == p for f in VERTEX_EDGES[v] if f != e):
                return None
        return f"path {edge_name(e)} is not connected to your road network"

    def legal_settlements(self, p: int, setup: bool = False) -> list[int]:
        return [v for v in range(NUM_VERTICES) if self.can_place_settlement(p, v, setup) is None]

    def legal_roads(self, p: int) -> list[int]:
        return [e for e in range(NUM_EDGES) if self.can_place_road(p, e) is None]

    def legal_cities(self, p: int) -> list[int]:
        return sorted(v for v, (o, k) in self.buildings.items() if o == p and k == "settlement")

    def playable_devs(self, p: int) -> list[str]:
        """Development cards p could play right now."""
        if p != self.current or self.dev_played or self.phase not in ("roll", "main"):
            return []
        pl = self.players[p]
        out = []
        for card in DEV_COUNTS:
            if card == "victory_point":
                continue
            if pl.devs.count(card) > pl.new_devs.count(card):
                out.append(card)
        return out

    def robber_victims(self, p: int, h: int) -> list[int]:
        victims = set()
        for v in HEX_VERTICES[h]:
            b = self.buildings.get(v)
            if b and b[0] != p and self.players[b[0]].num_cards > 0:
                victims.add(b[0])
        return sorted(victims)

    # ------------------------------------------------------------------ helpers
    def _require(self, p: int, *phases: str):
        if self.phase == "game_over":
            raise IllegalAction("the game is over")
        if self.phase not in phases:
            raise IllegalAction(self._phase_hint(p))
        if p not in self.pending():
            raise IllegalAction(f"it is not {self.name(p)}'s move")

    def _phase_hint(self, p: int) -> str:
        ph = self.phase
        if ph == "setup":
            return f"setup: {self.name(self.current)} must place a {self.setup_expect}"
        if ph == "roll":
            return f"{self.name(self.current)} must roll first"
        if ph == "discard":
            return "waiting for players to discard: " + ", ".join(
                f"{self.name(q)} ({k})" for q, k in self.discards.items())
        if ph == "robber":
            return f"{self.name(self.current)} must move the robber"
        if ph == "road_building":
            return f"{self.name(self.current)} must place {self.free_roads} free road(s)"
        if ph == "trade":
            return "a trade offer is pending"
        return f"not allowed now (phase: {ph})"

    def _pay(self, p: int, cost: dict):
        pl = self.players[p]
        if not pl.has(cost):
            missing = {r: n - pl.hand[r] for r, n in cost.items() if pl.hand[r] < n}
            raise IllegalAction(f"not enough resources (missing {fmt_cards(missing)})")
        for r, k in cost.items():
            pl.hand[r] -= k
            self.bank[r] += k

    def _give(self, p: int, res: str, k: int = 1):
        self.players[p].hand[res] += k
        self.bank[res] -= k

    def _check_win(self):
        if self.phase != "game_over" and self.total_vp(self.current) >= VP_TO_WIN:
            self.winner = self.current
            self.phase = "game_over"
            self.trade = None
            pl = self.players[self.current]
            vps = pl.devs.count("victory_point")
            extra = f" (including {vps} Victory Point card{'s' * (vps != 1)})" if vps else ""
            self._log(f"*** {pl.name} wins with {self.total_vp(self.current)} victory points{extra}! ***")

    def _update_longest_road(self):
        lengths = [self.road_length(p) for p in range(self.n)]
        best = max(lengths)
        holder = self.longest_road
        if holder is not None and lengths[holder] >= LONGEST_ROAD_MIN and lengths[holder] == best:
            return
        qualifiers = [p for p in range(self.n) if lengths[p] == best and best >= LONGEST_ROAD_MIN]
        new = qualifiers[0] if len(qualifiers) == 1 else None
        if new == holder:
            return
        self.longest_road = new
        if new is not None:
            self._log(f"{self.name(new)} takes Longest Road ({best} segments, +2 VP).")
        else:
            self._log(f"{self.name(holder)} loses Longest Road; the card is set aside.")

    def _update_largest_army(self, p: int):
        k = self.players[p].knights
        holder = self.largest_army
        if holder == p or k < LARGEST_ARMY_MIN:
            return
        if holder is None or k > self.players[holder].knights:
            self.largest_army = p
            self._log(f"{self.name(p)} takes Largest Army ({k} knights, +2 VP).")

    def _finish_free_roads(self):
        if self.free_roads <= 0 or self.players[self.current].pieces["road"] == 0 \
                or not self.legal_roads(self.current):
            self.free_roads = 0
            self.phase = self.return_phase
            self.return_phase = None

    # ------------------------------------------------------------------ setup
    def place_settlement_setup(self, p: int, v: int):
        self._require(p, "setup")
        if self.setup_expect != "settlement":
            raise IllegalAction(f"place a road next to your settlement on {self.setup_vertex} first")
        reason = self.can_place_settlement(p, v, setup=True)
        if reason:
            raise IllegalAction(reason)
        self.buildings[v] = (p, "settlement")
        self.players[p].pieces["settlement"] -= 1
        self.setup_vertex = v
        self.setup_expect = "road"
        msg = f"{self.name(p)} placed a settlement on {v}."
        if self.setup_index >= self.n:
            got = Counter()
            for h in VERTEX_HEXES[v]:
                res = self.board.terrain[h]
                if res != DESERT and self.bank[res] > 0:
                    self._give(p, res)
                    got[res] += 1
            msg += f" Starting resources: {fmt_cards(got)}."
        self._log(msg)

    def place_road_setup(self, p: int, e: int):
        self._require(p, "setup")
        if self.setup_expect != "road":
            raise IllegalAction("place a settlement first")
        reason = self.can_place_road(p, e)
        if reason:
            raise IllegalAction(reason)
        self.roads[e] = p
        self.players[p].pieces["road"] -= 1
        self._log(f"{self.name(p)} placed a road on {edge_name(e)}.")
        self.setup_index += 1
        self.setup_vertex = None
        self.setup_expect = "settlement"
        if self.setup_index < len(self.setup_queue):
            self.current = self.setup_queue[self.setup_index]
        else:
            self.current = self.first
            self._start_turn()

    # ------------------------------------------------------------------ turn flow
    def _start_turn(self):
        self.turn += 1
        self.phase = "roll"
        self.rolled = False
        self.dev_played = False
        self.trades_proposed = 0
        self._log(f"== Turn {self.turn}: {self.name(self.current)} ==")
        self._check_win()

    def roll(self, p: int, dice: tuple[int, int] | None = None):
        if self.rolled and p == self.current and self.phase != "game_over":
            raise IllegalAction("you have already rolled this turn")
        self._require(p, "roll")
        d1, d2 = dice if dice else (self.rng.randint(1, 6), self.rng.randint(1, 6))
        total = d1 + d2
        self.last_roll = (d1, d2)
        self.rolled = True
        self._log(f"{self.name(p)} rolled {total} ({d1}+{d2}).")
        if total == 7:
            self.discards = {q: pl.num_cards // 2 for q, pl in enumerate(self.players)
                             if pl.num_cards > 7}
            for q, k in self.discards.items():
                self._log(f"{self.name(q)} has {self.players[q].num_cards} cards and must discard {k}.")
            self.return_phase = "main"
            self.phase = "discard" if self.discards else "robber"
        else:
            self._produce(total)
            self.phase = "main"

    def _produce(self, total: int):
        owed = [Counter() for _ in self.players]
        for h in self.board.hexes_with_number(total):
            if h == self.robber:
                self._log(f"The robber blocks {hex_label(h)} ({self.board.terrain[h]}).")
                continue
            res = self.board.terrain[h]
            for v in HEX_VERTICES[h]:
                b = self.buildings.get(v)
                if b:
                    owed[b[0]][res] += 2 if b[1] == "city" else 1
        gains = [Counter() for _ in self.players]
        for res in RESOURCES:
            want = {q: owed[q][res] for q in range(self.n) if owed[q][res]}
            if not want:
                continue
            if sum(want.values()) <= self.bank[res]:
                for q, k in want.items():
                    gains[q][res] += k
            elif len(want) == 1:
                q = next(iter(want))
                if self.bank[res]:
                    gains[q][res] += self.bank[res]
                self._log(f"The bank is short of {res}; {self.name(q)} gets only {self.bank[res]}.")
            else:
                self._log(f"The bank is short of {res}; nobody receives {res}.")
        parts = []
        for q in range(self.n):
            for res, k in gains[q].items():
                self._give(q, res, k)
            if gains[q]:
                parts.append(f"{self.name(q)} +{fmt_cards(gains[q]).replace(', ', ' +')}")
        self._log("  " + ("; ".join(parts) if parts else "Nobody produces anything."))

    def discard(self, p: int, cards: dict):
        self._require(p, "discard")
        need = self.discards[p]
        cards = {r: k for r, k in cards.items() if k}
        if sum(cards.values()) != need:
            raise IllegalAction(f"{self.name(p)} must discard exactly {need} cards "
                                f"(got {sum(cards.values())})")
        pl = self.players[p]
        if not pl.has(cards):
            raise IllegalAction("you don't have those cards")
        for r, k in cards.items():
            pl.hand[r] -= k
            self.bank[r] += k
        del self.discards[p]
        self._log(f"{self.name(p)} discarded {fmt_cards(cards)}.")
        if not self.discards:
            self.phase = "robber"

    def check_robber_move(self, p: int, h: int, victim: int | None) -> int | None:
        """Validate a robber move; returns the resolved victim (or None)."""
        if not 0 <= h < NUM_HEXES:
            raise IllegalAction("no such hex")
        if h == self.robber:
            raise IllegalAction(f"the robber is already on {hex_label(h)}; move it elsewhere")
        victims = self.robber_victims(p, h)
        if victim is None:
            if len(victims) > 1:
                raise IllegalAction(f"choose whom to rob on {hex_label(h)}: "
                                    + ", ".join(self.name(q) for q in victims))
            return victims[0] if victims else None
        if victim not in victims:
            if not victims:
                raise IllegalAction(f"there is nobody to rob on {hex_label(h)}")
            raise IllegalAction(f"{self.name(victim)} can't be robbed on {hex_label(h)}; "
                                "choose from " + ", ".join(self.name(q) for q in victims))
        return victim

    def move_robber(self, p: int, h: int, victim: int | None = None):
        self._require(p, "robber")
        victim = self.check_robber_move(p, h, victim)
        self.robber = h
        terrain = self.board.terrain[h]
        num = self.board.numbers[h]
        where = f"{hex_label(h)} ({terrain}{' ' + str(num) if num else ''})"
        if victim is None:
            self._log(f"{self.name(p)} moved the robber to {where}; nobody to rob.")
        else:
            vp = self.players[victim]
            pool = [r for r in RESOURCES for _ in range(vp.hand[r])]
            res = self.rng.choice(pool)
            vp.hand[res] -= 1
            self.players[p].hand[res] += 1
            self._log(f"{self.name(p)} moved the robber to {where} and stole a card from {vp.name}.",
                      f"{self.name(p)} moved the robber to {where} and stole 1 {res} from {vp.name}.",
                      to=[p, victim])
        self.phase = self.return_phase
        self.return_phase = None

    def end_turn(self, p: int):
        self._require(p, "main")
        pl = self.players[p]
        pl.new_devs = []
        self._log(f"{self.name(p)} ended the turn.")
        self.current = (self.current + 1) % self.n
        self._start_turn()

    # ------------------------------------------------------------------ building
    def build_road(self, p: int, e: int):
        self._require(p, "main", "road_building")
        pl = self.players[p]
        if pl.pieces["road"] == 0:
            raise IllegalAction("you have no roads left")
        reason = self.can_place_road(p, e)
        if reason:
            raise IllegalAction(reason)
        free = self.phase == "road_building"
        if not free:
            self._pay(p, COSTS["road"])
        self.roads[e] = p
        pl.pieces["road"] -= 1
        self._log(f"{self.name(p)} built a road on {edge_name(e)}{' (free)' if free else ''}.")
        if free:
            self.free_roads -= 1
            self._finish_free_roads()
        self._update_longest_road()
        self._check_win()

    def end_road_building(self, p: int):
        self._require(p, "road_building")
        self._log(f"{self.name(p)} stops placing free roads.")
        self.free_roads = 0
        self._finish_free_roads()

    def build_settlement(self, p: int, v: int):
        self._require(p, "main")
        pl = self.players[p]
        if pl.pieces["settlement"] == 0:
            raise IllegalAction("you have no settlements left (upgrade one to a city)")
        reason = self.can_place_settlement(p, v)
        if reason:
            raise IllegalAction(reason)
        self._pay(p, COSTS["settlement"])
        self.buildings[v] = (p, "settlement")
        pl.pieces["settlement"] -= 1
        self._log(f"{self.name(p)} built a settlement on {v}.")
        self._update_longest_road()
        self._check_win()

    def build_city(self, p: int, v: int):
        self._require(p, "main")
        pl = self.players[p]
        if self.buildings.get(v) != (p, "settlement"):
            raise IllegalAction(f"you don't have a settlement on {v}")
        if pl.pieces["city"] == 0:
            raise IllegalAction("you have no cities left")
        self._pay(p, COSTS["city"])
        self.buildings[v] = (p, "city")
        pl.pieces["city"] -= 1
        pl.pieces["settlement"] += 1
        self._log(f"{self.name(p)} upgraded {v} to a city.")
        self._check_win()

    def buy_dev(self, p: int):
        self._require(p, "main")
        if not self.dev_deck:
            raise IllegalAction("the development card deck is empty")
        self._pay(p, COSTS["dev"])
        card = self.dev_deck.pop()
        pl = self.players[p]
        pl.devs.append(card)
        pl.new_devs.append(card)
        self._log(f"{self.name(p)} bought a development card.",
                  f"{self.name(p)} bought a development card: {DEV_NAMES[card]}.", to=[p])
        self._check_win()

    # ------------------------------------------------------------------ dev cards
    def _start_dev(self, p: int, card: str):
        self._require(p, "roll", "main")
        if self.dev_played:
            raise IllegalAction("you have already played a development card this turn")
        pl = self.players[p]
        if card not in pl.devs:
            raise IllegalAction(f"you don't have a {DEV_NAMES[card]} card")
        if pl.devs.count(card) <= pl.new_devs.count(card):
            raise IllegalAction(f"you can't play a {DEV_NAMES[card]} on the turn you bought it")

    def _use_dev(self, p: int, card: str):
        self.players[p].devs.remove(card)
        self.dev_played = True

    def play_knight(self, p: int, h: int | None = None, victim: int | None = None):
        self._start_dev(p, "knight")
        if h is not None:
            self.check_robber_move(p, h, victim)
        self._use_dev(p, "knight")
        self.players[p].knights += 1
        self._log(f"{self.name(p)} played a Knight.")
        self._update_largest_army(p)
        self.return_phase = self.phase
        self.phase = "robber"
        if h is not None:
            self.move_robber(p, h, victim)
        self._check_win()

    def play_road_building(self, p: int):
        self._start_dev(p, "road_building")
        self._use_dev(p, "road_building")
        self.free_roads = min(2, self.players[p].pieces["road"])
        self._log(f"{self.name(p)} played Road Building.")
        self.return_phase = self.phase
        self.phase = "road_building"
        self._finish_free_roads()

    def play_year_of_plenty(self, p: int, r1: str, r2: str):
        self._start_dev(p, "year_of_plenty")
        want = Counter([r1, r2])
        for r, k in want.items():
            if r not in RESOURCES:
                raise IllegalAction(f"unknown resource {r}")
            if self.bank[r] < k:
                raise IllegalAction(f"the bank only has {self.bank[r]} {r}")
        self._use_dev(p, "year_of_plenty")
        for r, k in want.items():
            self._give(p, r, k)
        self._log(f"{self.name(p)} played Year of Plenty and took {fmt_cards(want)}.")

    def play_monopoly(self, p: int, res: str):
        self._start_dev(p, "monopoly")
        if res not in RESOURCES:
            raise IllegalAction(f"unknown resource {res}")
        self._use_dev(p, "monopoly")
        taken = []
        total = 0
        for q, pl in enumerate(self.players):
            if q != p and pl.hand[res]:
                k = pl.hand[res]
                pl.hand[res] = 0
                total += k
                taken.append(f"{k} from {pl.name}")
        self.players[p].hand[res] += total
        self._log(f"{self.name(p)} played Monopoly on {res} and collected {total}"
                  + (f" ({', '.join(taken)})." if taken else "."))

    # ------------------------------------------------------------------ trading
    def maritime_trade(self, p: int, give: str, get: str, times: int = 1):
        self._require(p, "main")
        if give not in RESOURCES or get not in RESOURCES:
            raise IllegalAction("unknown resource")
        if give == get:
            raise IllegalAction("trade for a different resource")
        if times < 1:
            raise IllegalAction("nothing to trade")
        ratio = self.trade_ratio(p, give)
        need = ratio * times
        if self.players[p].hand[give] < need:
            raise IllegalAction(f"you need {need} {give} to trade at {ratio}:1 "
                                f"(you have {self.players[p].hand[give]})")
        if self.bank[get] < times:
            raise IllegalAction(f"the bank only has {self.bank[get]} {get}")
        self.players[p].hand[give] -= need
        self.bank[give] += need
        self._give(p, get, times)
        self._log(f"{self.name(p)} traded {need} {give} for {times} {get} with the bank ({ratio}:1).")

    def propose_trade(self, p: int, give: dict, get: dict, to: int | None = None):
        self._require(p, "main")
        give = {r: k for r, k in give.items() if k > 0}
        get = {r: k for r, k in get.items() if k > 0}
        if not give or not get:
            raise IllegalAction("both sides of a trade must include at least one card")
        if set(give) & set(get):
            raise IllegalAction("you can't trade a resource for the same resource")
        if not self.players[p].has(give):
            raise IllegalAction("you don't have the cards you're offering")
        if to is not None and (to == p or not 0 <= to < self.n):
            raise IllegalAction("invalid trade partner")
        targets = [to] if to is not None else [(p + i) % self.n for i in range(1, self.n)]
        self.trade = {"from": p, "give": give, "get": get, "targets": targets,
                      "responses": {}, "stage": "respond"}
        self.trades_proposed += 1
        self.return_phase = "main"
        self.phase = "trade"
        who = self.name(to) if to is not None else "everyone"
        self._log(f"{self.name(p)} offers {fmt_cards(give)} for {fmt_cards(get)} to {who}.")
        for q in targets:
            if not self.players[q].has(get):
                self.trade["responses"][str(q)] = False
                self._log(f"  {self.name(q)} declines.")
        self._resolve_trade()

    def respond_trade(self, q: int, accept: bool):
        self._require(q, "trade")
        t = self.trade
        if t["stage"] != "respond":
            raise IllegalAction("responses are closed")
        if accept and not self.players[q].has(t["get"]):
            raise IllegalAction("you don't have the cards requested")
        t["responses"][str(q)] = bool(accept)
        self._log(f"  {self.name(q)} {'accepts' if accept else 'declines'}.")
        self._resolve_trade()

    def trade_acceptors(self) -> list[int]:
        if not self.trade:
            return []
        return [q for q in self.trade["targets"] if self.trade["responses"].get(str(q))]

    def _resolve_trade(self):
        t = self.trade
        if any(str(q) not in t["responses"] for q in t["targets"]):
            return
        acc = self.trade_acceptors()
        if not acc:
            self._log("  No one accepted the offer.")
            self._close_trade()
        elif len(acc) == 1:
            self._execute_trade(acc[0])
        else:
            t["stage"] = "choose"
            self._log(f"  {self.name(t['from'])} chooses a partner: "
                      + ", ".join(self.name(q) for q in acc))

    def confirm_trade(self, p: int, q: int):
        self._require(p, "trade")
        if self.trade["stage"] != "choose":
            raise IllegalAction("still waiting for responses")
        if q not in self.trade_acceptors():
            raise IllegalAction(f"{self.name(q)} did not accept")
        self._execute_trade(q)

    def cancel_trade(self, p: int):
        if self.phase != "trade" or self.trade["from"] != p:
            raise IllegalAction("you have no open trade offer")
        self._log(f"  {self.name(p)} withdraws the offer.")
        self._close_trade()

    def _execute_trade(self, q: int):
        t = self.trade
        p = t["from"]
        a, b = self.players[p], self.players[q]
        if not a.has(t["give"]) or not b.has(t["get"]):
            self._log("  The trade fell through.")
            self._close_trade()
            return
        for r, k in t["give"].items():
            a.hand[r] -= k
            b.hand[r] += k
        for r, k in t["get"].items():
            b.hand[r] -= k
            a.hand[r] += k
        self._log(f"  {a.name} traded {fmt_cards(t['give'])} to {b.name} for {fmt_cards(t['get'])}.")
        self._close_trade()

    def _close_trade(self):
        self.trade = None
        self.phase = self.return_phase or "main"
        self.return_phase = None

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        st = self.rng.getstate()
        return {
            "version": 1,
            "seed": self.seed,
            "layout": self.layout,
            "rng": [st[0], list(st[1]), st[2]],
            "board": self.board.to_dict(),
            "players": [pl.to_dict() for pl in self.players],
            "bank": self.bank,
            "dev_deck": self.dev_deck,
            "buildings": {str(v): list(b) for v, b in self.buildings.items()},
            "roads": {str(e): o for e, o in self.roads.items()},
            "robber": self.robber,
            "first": self.first,
            "current": self.current,
            "setup_queue": self.setup_queue,
            "setup_index": self.setup_index,
            "setup_expect": self.setup_expect,
            "setup_vertex": self.setup_vertex,
            "phase": self.phase,
            "turn": self.turn,
            "rolled": self.rolled,
            "dev_played": self.dev_played,
            "last_roll": self.last_roll,
            "discards": {str(k): v for k, v in self.discards.items()},
            "free_roads": self.free_roads,
            "return_phase": self.return_phase,
            "trade": self.trade,
            "trades_proposed": self.trades_proposed,
            "longest_road": self.longest_road,
            "largest_army": self.largest_army,
            "winner": self.winner,
            "log": self.log,
            "options": self.options,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Game":
        g = cls.__new__(cls)
        g.seed = d["seed"]
        g.layout = d["layout"]
        g.rng = random.Random()
        r = d["rng"]
        g.rng.setstate((r[0], tuple(r[1]), r[2]))
        g.board = Board.from_dict(d["board"])
        g.players = [Player.from_dict(x) for x in d["players"]]
        g.bank = dict(d["bank"])
        g.dev_deck = list(d["dev_deck"])
        g.buildings = {int(v): (b[0], b[1]) for v, b in d["buildings"].items()}
        g.roads = {int(e): o for e, o in d["roads"].items()}
        for key in ("robber", "first", "current", "setup_index", "setup_expect", "setup_vertex",
                    "phase", "turn", "rolled", "dev_played", "free_roads", "return_phase",
                    "trade", "trades_proposed", "longest_road", "largest_army", "winner"):
            setattr(g, key, d[key])
        g.setup_queue = list(d["setup_queue"])
        g.last_roll = tuple(d["last_roll"]) if d["last_roll"] else None
        g.discards = {int(k): v for k, v in d["discards"].items()}
        g.log = list(d["log"])
        g.options = dict(d.get("options", {}))
        g.meta = dict(d.get("meta", {}))
        return g

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, separators=(",", ":"))

    @classmethod
    def load(cls, path: str) -> "Game":
        with open(path) as f:
            return cls.from_dict(json.load(f))

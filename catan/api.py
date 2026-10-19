"""JSON view of a game from one seat, for the web UI (and `catan show --json`).

Only information that seat is allowed to see is included: other players'
hands and development cards appear as counts.
"""

from __future__ import annotations

from .board import (EDGES, HEX_CENTERS, NUM_HEXES, PIPS, PORT_EDGES, PORT_SLOTS, RESOURCES,
                    VERTEX_POS, hex_label)
from .game import COSTS, DEV_NAMES, Game

GEOMETRY = {
    "hexes": [list(c) for c in HEX_CENTERS],
    "hexLabels": [hex_label(h) for h in range(NUM_HEXES)],
    "vertices": [list(p) for p in VERTEX_POS],
    "edges": [list(e) for e in EDGES],
    "portSlots": [{"a": a, "b": b, "hex": h, "side": side}
                  for (h, side), (a, b) in zip(PORT_SLOTS, PORT_EDGES)],
    "pips": PIPS,
}


def seat_kind(g: Game, p: int) -> str:
    pl = g.players[p]
    return "bot" if pl.bot else ("agent" if pl.agent else "human")


def default_seat(g: Game) -> int | None:
    """The seat a browser should play: the first human (non-bot, non-agent) one."""
    for p, pl in enumerate(g.players):
        if not pl.bot and not pl.agent:
            return p
    return None


def view_state(g: Game, seat: int | None, log_tail: int = 80) -> dict:
    from .cli import Session  # the CLI owns the wording of hints

    pend = g.pending()
    over = g.phase == "game_over"
    your_move = seat is not None and seat in pend
    players = []
    for p, pl in enumerate(g.players):
        players.append({
            "name": pl.name,
            "color": pl.color,
            "kind": seat_kind(g, p),
            "vp": g.total_vp(p) if (p == seat or over) else g.public_vp(p),
            "cards": pl.num_cards,
            "devs": len(pl.devs),
            "knights": pl.knights,
            "road": g.road_length(p),
            "pieces": pl.pieces,
            "ports": g.player_ports(p),
            "longestRoad": g.longest_road == p,
            "largestArmy": g.largest_army == p,
            "pending": p in pend,
            "current": p == g.current and not over,
            "hand": pl.hand if over else None,
        })

    you = None
    if seat is not None:
        pl = g.players[seat]
        playable = set(g.playable_devs(seat))
        new = list(pl.new_devs)
        devs = []
        for c in pl.devs:
            is_new = c in new
            if is_new:
                new.remove(c)
            devs.append({"card": c, "name": DEV_NAMES[c], "new": is_new,
                         "playable": c in playable})
        you = {
            "seat": seat,
            "hand": pl.hand,
            "devs": devs,
            "vp": g.total_vp(seat),
            "ratios": {r: g.trade_ratio(seat, r) for r in RESOURCES},
            "production": g.production(seat),
            "pieces": pl.pieces,
        }

    legal: dict = {}
    if your_move:
        pl = g.players[seat]
        ph = g.phase
        if ph == "setup":
            if g.setup_expect == "settlement":
                legal["settlements"] = g.legal_settlements(seat, setup=True)
            else:
                legal["roads"] = [list(EDGES[e]) for e in g.legal_roads(seat)]
        if ph in ("main", "road_building") and pl.pieces["road"]:
            legal["roads"] = [list(EDGES[e]) for e in g.legal_roads(seat)]
        if ph == "main":
            legal["settlements"] = g.legal_settlements(seat) if pl.pieces["settlement"] else []
            legal["cities"] = g.legal_cities(seat) if pl.pieces["city"] else []
            legal["afford"] = {b: pl.has(cost) for b, cost in COSTS.items()}
            legal["canBuyDev"] = bool(g.dev_deck) and pl.has(COSTS["dev"])
        if ph in ("roll", "main"):
            legal["playable"] = g.playable_devs(seat)
        if ph == "robber" or "knight" in legal.get("playable", []):
            legal["robber"] = {str(h): g.robber_victims(seat, h)
                               for h in range(NUM_HEXES) if h != g.robber}
        if ph == "discard":
            legal["discard"] = g.discards[seat]

    trade = None
    if g.trade:
        t = g.trade
        trade = {
            "from": t["from"], "give": t["give"], "get": t["get"], "targets": t["targets"],
            "responses": {int(k): v for k, v in t["responses"].items()},
            "stage": t["stage"], "acceptors": g.trade_acceptors(),
        }

    session = Session(g, None, color=False, write=lambda *_: None)
    if over:
        hint = f"{g.name(g.winner)} won the game."
    elif your_move:
        hint = session.next_hint(seat)
    else:
        hint = "Waiting for " + ", ".join(g.name(p) for p in pend) + "."

    return {
        "geometry": GEOMETRY,
        "seat": seat,
        "terrain": g.board.terrain,
        "numbers": g.board.numbers,
        "portTypes": g.board.port_types,
        "robber": g.robber,
        "buildings": [[v, o, k] for v, (o, k) in sorted(g.buildings.items())],
        "roads": [list(EDGES[e]) + [o] for e, o in sorted(g.roads.items())],
        "players": players,
        "phase": g.phase,
        "turn": g.turn,
        "current": g.current,
        "pending": pend,
        "rolled": g.rolled,
        "lastRoll": list(g.last_roll) if g.last_roll else None,
        "setupExpect": g.setup_expect if g.phase == "setup" else None,
        "setupVertex": g.setup_vertex,
        "setupRound": 1 + g.setup_index // g.n if g.phase == "setup" else None,
        "freeRoads": g.free_roads,
        "discards": {str(k): v for k, v in g.discards.items()},
        "bank": g.bank,
        "devDeck": len(g.dev_deck),
        "trade": trade,
        "winner": g.winner,
        "you": you,
        "yourMove": your_move,
        "legal": legal,
        "hint": hint,
        "log": [g.log_text(e, seat) for e in g.log[-log_tail:]],
        "costs": COSTS,
        "seed": g.seed,
    }

"""Text rendering of the board and game status (ANSI colour optional)."""

from __future__ import annotations

from .board import (CORNER_OFFSETS, DESERT, EDGES, HEX_CENTERS, NUM_HEXES, PIPS, PORT_EDGES,
                    PORT_SLOTS, RESOURCES, VERTEX_POS, hex_label)
from .game import DEV_NAMES, Game

MX, MY = 2, 1          # margins (columns, lines)
XS, YS = 6, 2          # characters per lattice unit
WIDTH = MX + 6 * 10 + 10
HEIGHT = MY + 2 * 16 + 2

RES_STYLE = {"brick": "38;5;166", "lumber": "38;5;34", "wool": "38;5;119",
             "grain": "38;5;220", "ore": "38;5;248", DESERT: "38;5;180"}
PLAYER_BG = {"red": "1;97;41", "blue": "1;97;44", "orange": "1;30;48;5;208", "white": "1;30;47"}
PLAYER_FG = {"red": "1;31", "blue": "1;34", "orange": "1;38;5;208", "white": "1;37"}
DIM = "38;5;242"
PORT_STYLE = "36"
HOT = "1;31"
BOLD = "1"
ROBBER_STYLE = "1;30;43"


def paint(text: str, style: str | None, color: bool) -> str:
    if not color or not style:
        return text
    return f"\x1b[{style}m{text}\x1b[0m"


def player_tag(g: Game, p: int, color: bool) -> str:
    pl = g.players[p]
    return paint(pl.name, PLAYER_FG.get(pl.color), color)


class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.cells = [[(" ", None)] * w for _ in range(h)]

    def put(self, x: int, y: int, text: str, style: str | None = None):
        if not 0 <= y < self.h:
            return
        for i, ch in enumerate(text):
            if 0 <= x + i < self.w:
                self.cells[y][x + i] = (ch, style)

    def center(self, cx: int, y: int, text: str, style: str | None = None):
        self.put(cx - (len(text) - 1) // 2, y, text, style)

    def render(self, color: bool) -> str:
        lines = []
        for row in self.cells:
            out, run, cur = [], [], None
            for ch, st in row:
                if st != cur and run:
                    out.append(paint("".join(run), cur, color))
                    run = []
                cur = st
                run.append(ch)
            if run:
                out.append(paint("".join(run), cur, color))
            lines.append("".join(out).rstrip() if not color else "".join(out))
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(line.rstrip() for line in lines)


def _col(x: int) -> int:
    return MX + XS * x


def _line(y: int) -> int:
    return MY + YS * y


def render_board(g: Game, color: bool = True, ascii_only: bool = False) -> str:
    cv = Canvas(WIDTH, HEIGHT)
    pip = "*" if ascii_only else "•"
    board = g.board

    # Hex interiors.
    for h in range(NUM_HEXES):
        cx, cy = _col(HEX_CENTERS[h][0]), _line(HEX_CENTERS[h][1])
        res = board.terrain[h]
        num = board.numbers[h]
        cv.center(cx, cy - 2, hex_label(h), BOLD)
        cv.center(cx, cy - 1, res, RES_STYLE[res])
        if num:
            cv.center(cx, cy, str(num), HOT if num in (6, 8) else BOLD)
            cv.center(cx, cy + 1, pip * PIPS[num], HOT if num in (6, 8) else DIM)
        if g.robber == h:
            cv.center(cx, cy + 2, "ROBBER", ROBBER_STYLE)

    # Edges and roads.
    for e, (a, b) in enumerate(EDGES):
        (ax, ay), (bx, by) = VERTEX_POS[a], VERTEX_POS[b]
        owner = g.roads.get(e)
        style = PLAYER_BG[g.players[owner].color] if owner is not None else DIM
        letter = g.players[owner].color[0] if owner is not None else None
        if ax == bx:
            x = _col(ax)
            y0, y1 = sorted((_line(ay), _line(by)))
            for i, y in enumerate(range(y0 + 1, y1)):
                ch = letter if (letter and i == (y1 - y0 - 2) // 2) else "|"
                cv.put(x, y, ch, style)
        else:
            mx = (_col(ax) + _col(bx)) // 2
            my = (_line(ay) + _line(by)) // 2
            top_x = ax if ay < by else bx
            other_x = bx if ay < by else ax
            ch = "/" if top_x > other_x else "\\"
            if letter:
                cv.put(mx - 1, my, ch + letter + ch, style)
            else:
                cv.put(mx, my, ch, style)

    # Harbours.
    for (h, side), (a, b), kind in zip(PORT_SLOTS, PORT_EDGES, board.port_types):
        cx, cy = _col(HEX_CENTERS[h][0]), _line(HEX_CENTERS[h][1])
        ratio = "3:1" if kind == "any" else "2:1"
        style = PORT_STYLE if kind == "any" else RES_STYLE[kind]
        if side in ("E", "W"):
            lines = [ratio, "any" if kind == "any" else kind]
            for i, text in enumerate(lines):
                if side == "E":
                    cv.put(cx + 9, cy - 1 + i, text, style)
                else:
                    cv.put(cx - 8 - len(text), cy - 1 + i, text, style)
        else:
            text = ratio if kind == "any" else f"{ratio} {kind}"
            outer_row = HEX_CENTERS[h][1] in (2, 14)   # top or bottom row
            y = cy + (5 if outer_row else 4) * (-1 if "N" in side else 1)
            x = cx + 4 if "E" in side else cx - 3 - len(text)
            cv.put(x, y, text, style)

    # Intersections (drawn last so they sit on top).
    for v, (x, y) in enumerate(VERTEX_POS):
        c, ln = _col(x), _line(y)
        b = g.buildings.get(v)
        if b:
            owner, kind = b
            pc = g.players[owner].color
            letter = pc[0].upper() if kind == "city" else pc[0]
            cv.put(c - 1, ln, f"{v:>2}{letter}", PLAYER_BG[pc])
        else:
            cv.put(c - 1, ln, f"{v:>2}", PORT_STYLE if v in board.vertex_port else DIM)
    return cv.render(color)


def legend(color: bool = True) -> str:
    return ("  Pieces: 12r = red settlement on 12, 12R = red city.  Roads show the owner's"
            " letter.\n  Cyan numbers touch a harbour.  Hexes are A-S; dots = odds of the number.")


def fmt_hand(hand: dict, color: bool = True) -> str:
    return "  ".join(paint(f"{r} {hand[r]}", RES_STYLE[r], color) for r in RESOURCES)


def short_hand(hand: dict) -> str:
    return " ".join(f"{r[0].upper()}{hand[r]}" for r in RESOURCES)


def dev_summary(g: Game, p: int) -> str:
    pl = g.players[p]
    if not pl.devs:
        return "none"
    counts = {}
    for c in pl.devs:
        counts[c] = counts.get(c, 0) + 1
    parts = []
    for c, k in counts.items():
        new = pl.new_devs.count(c)
        label = DEV_NAMES[c] + (f" x{k}" if k > 1 else "")
        if new and c != "victory_point":
            label += f" ({new} new)"
        parts.append(label)
    return ", ".join(parts)


def render_status(g: Game, viewer: int | None, color: bool = True) -> str:
    lines = []
    if g.phase == "setup":
        head = f"Setup round {1 + g.setup_index // g.n} · {player_tag(g, g.current, color)} to place a {g.setup_expect}"
    elif g.phase == "game_over":
        head = f"Game over after {g.turn} turns · winner: {player_tag(g, g.winner, color)}"
    else:
        head = f"Turn {g.turn} · {player_tag(g, g.current, color)} · phase: {g.phase}"
        if g.last_roll and g.rolled:
            head += f" · rolled {sum(g.last_roll)}"
    r = g.robber
    num = g.board.numbers[r]
    head += f" · robber on {hex_label(r)} ({g.board.terrain[r]}{' ' + str(num) if num else ''})"
    lines.append(head)
    lines.append("Bank: " + " ".join(f"{res[0].upper()}{g.bank[res]}" for res in RESOURCES)
                 + f" · dev deck {len(g.dev_deck)}")
    lines.append(f"  {'Player':<16}{'VP':>3} {'Cards':>5} {'Dev':>4} {'Knights':>8} {'Road':>5}"
                 f"  {'Left R/S/C':<11} Harbours")
    for p, pl in enumerate(g.players):
        tag = " (bot)" if pl.bot else (" (you)" if p == viewer else (" (agent)" if pl.agent else ""))
        mark = ">" if p == g.current and g.phase != "game_over" else " "
        name = pl.name + tag
        vp = g.total_vp(p) if (viewer is None or p == viewer or g.phase == "game_over") else g.public_vp(p)
        knights = f"{pl.knights}{'*' if g.largest_army == p else ''}"
        road = f"{g.road_length(p)}{'*' if g.longest_road == p else ''}"
        left = f"{pl.pieces['road']}/{pl.pieces['settlement']}/{pl.pieces['city']}"
        ports = ",".join("3:1" if k == "any" else f"2:1{k[0].upper()}" for k in g.player_ports(p)) or "-"
        row = (f"{mark} {name:<16}{vp:>3} {pl.num_cards:>5} {len(pl.devs):>4} {knights:>8} {road:>5}"
               f"  {left:<11} {ports}")
        lines.append(paint(row, PLAYER_FG.get(pl.color), color) if p == g.current else row)
    extras = []
    extras.append("Longest Road: " + (g.name(g.longest_road) if g.longest_road is not None else "-"))
    extras.append("Largest Army: " + (g.name(g.largest_army) if g.largest_army is not None else "-"))
    lines.append("  " + " · ".join(extras) + "   (* = holder)")
    shown = [viewer] if viewer is not None else list(range(g.n))
    for p in shown:
        pl = g.players[p]
        who = "Your" if viewer is not None else pl.name + "'s"
        lines.append(f"{who} hand: {fmt_hand(pl.hand, color)}  ({pl.num_cards} cards)"
                     f" · dev: {dev_summary(g, p)}")
        if viewer is not None:
            prod = g.production(p)
            lines.append("Production (pips/36 rolls): "
                         + " ".join(f"{res[0].upper()}{prod[res]}" for res in RESOURCES)
                         + " · bank rates: "
                         + " ".join(f"{res[0].upper()}{g.trade_ratio(p, res)}" for res in RESOURCES))
    return "\n".join(lines)


def render_ports(g: Game) -> str:
    lines = []
    for (a, b), kind in zip(PORT_EDGES, g.board.port_types):
        label = "3:1 any" if kind == "any" else f"2:1 {kind}"
        owners = []
        for v in (a, b):
            if v in g.buildings:
                owners.append(f"{g.name(g.buildings[v][0])} on {v}")
        lines.append(f"  {label:<10} intersections {a:>2} & {b:<2}  {', '.join(owners)}")
    return "\n".join(lines)


def render_vertex_info(g: Game, v: int, viewer: int | None) -> str:
    from .board import VERTEX_HEXES, VERTEX_NEIGHBORS
    parts = []
    for h in VERTEX_HEXES[v]:
        res = g.board.terrain[h]
        num = g.board.numbers[h]
        rob = " [robber]" if g.robber == h else ""
        parts.append(f"{hex_label(h)}: {res}" + (f" {num} ({PIPS[num]} pips)" if num else "") + rob)
    lines = [f"Intersection {v}: " + "; ".join(parts)]
    pips = sum(PIPS[g.board.numbers[h]] for h in VERTEX_HEXES[v] if g.board.numbers[h])
    lines.append(f"  total {pips} pips · neighbours {', '.join(map(str, VERTEX_NEIGHBORS[v]))}")
    if v in g.board.vertex_port:
        kind = g.board.vertex_port[v]
        lines.append("  harbour: " + ("3:1 any" if kind == "any" else f"2:1 {kind}"))
    b = g.buildings.get(v)
    if b:
        lines.append(f"  {g.name(b[0])}'s {b[1]}")
    elif viewer is not None:
        reason = g.can_place_settlement(viewer, v, setup=g.phase == "setup")
        lines.append("  you can settle here" if reason is None else f"  can't settle: {reason}")
    return "\n".join(lines)


def render_hex_info(g: Game, h: int) -> str:
    from .board import HEX_VERTICES
    res = g.board.terrain[h]
    num = g.board.numbers[h]
    lines = [f"Hex {hex_label(h)}: {res}" + (f" {num} ({PIPS[num]} pips)" if num else "")
             + (" — robber here" if g.robber == h else "")]
    lines.append("  corners: " + ", ".join(map(str, HEX_VERTICES[h])))
    occ = []
    for v in HEX_VERTICES[h]:
        b = g.buildings.get(v)
        if b:
            occ.append(f"{g.name(b[0])} {b[1]} on {v}")
    lines.append("  buildings: " + (", ".join(occ) if occ else "none"))
    return "\n".join(lines)

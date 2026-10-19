"""Board geometry and layout for the classic 19-hex Catan island.

Hexes are pointy-topped and laid out in rows of 3-4-5-4-3.  They are
identified by letters A..S in reading order.  The 54 intersections
(vertices) are numbered 0..53 top-to-bottom, left-to-right, and the 72
paths (edges) are identified by the pair of vertices they join.

Geometry is computed once at import time on a small integer lattice:
x is measured in half-hex widths, y in quarter-hex heights.
"""

from __future__ import annotations

import random

RESOURCES = ("brick", "lumber", "wool", "grain", "ore")
DESERT = "desert"

ROW_COUNTS = (3, 4, 5, 4, 3)
NUM_HEXES = sum(ROW_COUNTS)

# Pointy-top corner offsets in lattice units, clockwise from the top.
CORNER_NAMES = ("N", "NE", "SE", "S", "SW", "NW")
CORNER_OFFSETS = ((0, -2), (1, -1), (1, 1), (0, 2), (-1, 1), (-1, -1))
# Side k joins corner k and corner k+1.
SIDE_NAMES = ("NE", "E", "SE", "SW", "W", "NW")

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}

# Number tokens in alphabetical (A..R) order, placed in a spiral.
SPIRAL_TOKENS = (5, 2, 6, 3, 8, 10, 9, 12, 11, 4, 8, 10, 9, 4, 5, 6, 3, 11)
TERRAIN_COUNTS = {"brick": 3, "lumber": 4, "wool": 4, "grain": 4, "ore": 3, DESERT: 1}

# The nine harbours sit at fixed spots on the frame: (hex index, side).
# Listed clockwise starting at the top-left.
PORT_SLOTS = ((0, "NW"), (1, "NE"), (6, "NE"), (11, "E"), (15, "SE"),
              (17, "SE"), (16, "SW"), (12, "W"), (3, "W"))
PORT_TYPES = ("any", "any", "any", "any", "brick", "lumber", "wool", "grain", "ore")

# Fixed "starting set-up for beginners" terrain and numbers (reading order).
BEGINNER_TERRAIN = (
    "ore", "wool", "lumber",
    "grain", "brick", "wool", "brick",
    "grain", "lumber", DESERT, "lumber", "ore",
    "lumber", "ore", "grain", "wool",
    "brick", "grain", "wool",
)
BEGINNER_NUMBERS = (
    10, 2, 9,
    12, 6, 4, 10,
    9, 11, None, 3, 8,
    8, 3, 4, 5,
    5, 6, 11,
)
# Harbour types for the beginner board, in PORT_SLOTS order.
BEGINNER_PORTS = ("any", "wool", "any", "any", "brick", "lumber", "any", "grain", "ore")


def hex_label(h: int) -> str:
    return chr(ord("A") + h)


def parse_hex(token: str) -> int | None:
    t = token.strip().upper()
    if len(t) == 1 and "A" <= t <= chr(ord("A") + NUM_HEXES - 1):
        return ord(t) - ord("A")
    return None


def _build_geometry():
    hex_rc = []
    centers = []
    for r, count in enumerate(ROW_COUNTS):
        for i in range(count):
            hex_rc.append((r, i))
            centers.append((abs(2 - r) + 2 * i + 1, 3 * r + 2))

    # Collect corner positions and number them in reading order.
    positions = set()
    for cx, cy in centers:
        for dx, dy in CORNER_OFFSETS:
            positions.add((cx + dx, cy + dy))
    vertex_pos = sorted(positions, key=lambda p: (p[1], p[0]))
    vid = {p: i for i, p in enumerate(vertex_pos)}

    hex_vertices = []
    for cx, cy in centers:
        hex_vertices.append(tuple(vid[(cx + dx, cy + dy)] for dx, dy in CORNER_OFFSETS))

    edge_set = set()
    for verts in hex_vertices:
        for k in range(6):
            a, b = verts[k], verts[(k + 1) % 6]
            edge_set.add((min(a, b), max(a, b)))

    def mid(e):
        (ax, ay), (bx, by) = vertex_pos[e[0]], vertex_pos[e[1]]
        return (ay + by, ax + bx)

    edges = sorted(edge_set, key=mid)
    edge_id = {e: i for i, e in enumerate(edges)}

    nv = len(vertex_pos)
    vertex_hexes = [[] for _ in range(nv)]
    for h, verts in enumerate(hex_vertices):
        for v in verts:
            vertex_hexes[v].append(h)
    vertex_edges = [[] for _ in range(nv)]
    vertex_neighbors = [[] for _ in range(nv)]
    for i, (a, b) in enumerate(edges):
        vertex_edges[a].append(i)
        vertex_edges[b].append(i)
        vertex_neighbors[a].append(b)
        vertex_neighbors[b].append(a)

    hex_neighbors = []
    for h in range(len(centers)):
        hs = set(hex_vertices[h])
        hex_neighbors.append(tuple(g for g in range(len(centers))
                                   if g != h and len(hs & set(hex_vertices[g])) == 2))

    port_edges = []
    for h, side in PORT_SLOTS:
        k = SIDE_NAMES.index(side)
        a, b = hex_vertices[h][k], hex_vertices[h][(k + 1) % 6]
        port_edges.append((min(a, b), max(a, b)))

    return dict(
        hex_rc=tuple(hex_rc),
        hex_centers=tuple(centers),
        vertex_pos=tuple(vertex_pos),
        hex_vertices=tuple(hex_vertices),
        edges=tuple(edges),
        edge_id=edge_id,
        vertex_hexes=tuple(tuple(x) for x in vertex_hexes),
        vertex_edges=tuple(tuple(x) for x in vertex_edges),
        vertex_neighbors=tuple(tuple(x) for x in vertex_neighbors),
        hex_neighbors=tuple(hex_neighbors),
        port_edges=tuple(port_edges),
    )


_G = _build_geometry()
HEX_RC = _G["hex_rc"]
HEX_CENTERS = _G["hex_centers"]
VERTEX_POS = _G["vertex_pos"]
HEX_VERTICES = _G["hex_vertices"]
EDGES = _G["edges"]
EDGE_ID = _G["edge_id"]
VERTEX_HEXES = _G["vertex_hexes"]
VERTEX_EDGES = _G["vertex_edges"]
VERTEX_NEIGHBORS = _G["vertex_neighbors"]
HEX_NEIGHBORS = _G["hex_neighbors"]
PORT_EDGES = _G["port_edges"]
NUM_VERTICES = len(VERTEX_POS)
NUM_EDGES = len(EDGES)

# Spiral order for number tokens: outer ring counter-clockwise from a
# corner, then the inner ring, then the centre.
_OUTER_RING = (0, 3, 7, 12, 16, 17, 18, 15, 11, 6, 2, 1)
_INNER_RING = (4, 8, 13, 14, 10, 5)
_CENTRE = 9


def spiral_order(start_corner: int) -> list[int]:
    k = start_corner % 6
    return (list(_OUTER_RING[2 * k:] + _OUTER_RING[:2 * k])
            + list(_INNER_RING[k:] + _INNER_RING[:k]) + [_CENTRE])


def edge_between(a: int, b: int) -> int | None:
    return EDGE_ID.get((min(a, b), max(a, b)))


class Board:
    """A concrete layout: terrain and numbers per hex plus harbour types."""

    def __init__(self, terrain, numbers, port_types):
        self.terrain = list(terrain)
        self.numbers = list(numbers)
        self.port_types = list(port_types)
        self.vertex_port: dict[int, str] = {}
        for (a, b), kind in zip(PORT_EDGES, self.port_types):
            self.vertex_port[a] = kind
            self.vertex_port[b] = kind

    @property
    def desert(self) -> int:
        return self.terrain.index(DESERT)

    def hexes_with_number(self, n: int) -> list[int]:
        return [h for h, num in enumerate(self.numbers) if num == n]

    def red_numbers_adjacent(self) -> bool:
        for h, n in enumerate(self.numbers):
            if n in (6, 8):
                for g in HEX_NEIGHBORS[h]:
                    if self.numbers[g] in (6, 8):
                        return True
        return False

    @classmethod
    def beginner(cls) -> "Board":
        return cls(BEGINNER_TERRAIN, BEGINNER_NUMBERS, BEGINNER_PORTS)

    @classmethod
    def random(cls, rng: random.Random) -> "Board":
        """Variable set-up: shuffle terrain and harbours, lay the number
        tokens in the official spiral, and re-deal if two red numbers
        (6/8) end up next to each other."""
        tiles = [t for t, c in TERRAIN_COUNTS.items() for _ in range(c)]
        ports = list(PORT_TYPES)
        rng.shuffle(ports)
        while True:
            rng.shuffle(tiles)
            numbers = [None] * NUM_HEXES
            tokens = iter(SPIRAL_TOKENS)
            for h in spiral_order(rng.randrange(6)):
                if tiles[h] != DESERT:
                    numbers[h] = next(tokens)
            board = cls(tiles, numbers, ports)
            if not board.red_numbers_adjacent():
                return board

    @classmethod
    def generate(cls, layout: str, rng: random.Random) -> "Board":
        if layout == "beginner":
            return cls.beginner()
        if layout == "random":
            return cls.random(rng)
        raise ValueError(f"unknown layout {layout!r} (use 'random' or 'beginner')")

    def to_dict(self) -> dict:
        return {"terrain": self.terrain, "numbers": self.numbers, "ports": self.port_types}

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        return cls(d["terrain"], d["numbers"], d["ports"])

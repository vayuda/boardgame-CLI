"""Heuristic computer opponents.

Bots only use information a real player would have: the board, public
counts (cards in hand, VP shown), and their own hand.  `act(game, p)`
performs exactly one move for player p, who must be in game.pending().
"""

from __future__ import annotations

from .board import (DESERT, EDGES, HEX_VERTICES, NUM_HEXES, PIPS, RESOURCES, VERTEX_EDGES,
                    VERTEX_HEXES, VERTEX_NEIGHBORS)
from .game import COSTS, Game, IllegalAction

RES_WEIGHT = {"brick": 1.05, "lumber": 1.05, "wool": 0.85, "grain": 1.0, "ore": 0.95}
MAX_ACTIONS_PER_TURN = 60


# ---------------------------------------------------------------- evaluation
VARIETY = True   # value resource variety, not just raw dice odds


def vertex_value(g: Game, p: int, v: int) -> float:
    prod = g.production(p)
    val = 0.0
    new_res = set()
    local = {r: 0 for r in RESOURCES}
    for h in VERTEX_HEXES[v]:
        res = g.board.terrain[h]
        if res == DESERT:
            continue
        pips = PIPS[g.board.numbers[h]] * (0.6 if h == g.robber else 1.0)
        weight = RES_WEIGHT[res]
        if VARIETY:
            # Diminishing returns: more of a resource you already get is worth less.
            weight /= 1 + (prod[res] + local[res]) / 8
        val += pips * weight
        local[res] += pips
        if prod[res] == 0:
            new_res.add(res)
    val += (2.0 if VARIETY else 1.2) * len(new_res)
    port = g.board.vertex_port.get(v)
    if port == "any":
        val += 1.0
    elif port:
        val += 0.3 * (prod[port] + local[port])
    return val


def missing(hand: dict, cost: dict) -> dict:
    return {r: k - hand[r] for r, k in cost.items() if hand[r] < k}


def hand_utility(g: Game, p: int, hand: dict) -> float:
    pl = g.players[p]
    weights = {
        "settlement": 1.0 if pl.pieces["settlement"] else 0.1,
        "city": 1.0 if g.legal_cities(p) and pl.pieces["city"] else 0.3,
        "dev": 0.5 if g.dev_deck else 0.0,
        "road": 0.35 if pl.pieces["road"] else 0.0,
    }
    u = 0.0
    for build, w in weights.items():
        cost = COSTS[build]
        have = sum(min(hand[r], k) for r, k in cost.items())
        u += w * (have / sum(cost.values())) ** 2
    return u + 0.02 * sum(hand.values())


def road_target(g: Game, p: int, max_depth: int = 3):
    """Best reachable empty settlement spot: (vertex, first edge, distance)."""
    start = {v for v, (o, _) in g.buildings.items() if o == p}
    for e, o in g.roads.items():
        if o == p:
            start.update(v for v in EDGES[e] if not g._blocked_for(p, v))
    dist = {v: 0 for v in start}
    first = {v: None for v in start}
    frontier = list(start)
    for depth in range(1, max_depth + 1):
        nxt = []
        for v in frontier:
            for e in VERTEX_EDGES[v]:
                if e in g.roads:
                    continue
                a, b = EDGES[e]
                u = b if a == v else a
                if u in dist or g._blocked_for(p, u):
                    continue
                dist[u] = depth
                first[u] = first[v] if first[v] is not None else e
                nxt.append(u)
        frontier = nxt
    best = None
    for u, d in dist.items():
        if d == 0 or g.can_place_settlement(p, u, setup=True) is not None:
            continue
        score = vertex_value(g, p, u) / (d + 0.5)
        if best is None or score > best[0]:
            best = (score, u, first[u], d)
    return best[1:] if best else None


def best_road_for_length(g: Game, p: int):
    best, best_len = None, -1
    for e in g.legal_roads(p):
        g.roads[e] = p
        length = g.road_length(p)
        del g.roads[e]
        if length > best_len:
            best, best_len = e, length
    return best


def robber_ranking(g: Game, p: int) -> list[tuple[float, int, int | None]]:
    """Robber targets for p, best first: (score, hex, victim)."""
    ranked = []
    for h in range(NUM_HEXES):
        if h == g.robber:
            continue
        num = g.board.numbers[h]
        score = 0.0
        own = False
        for v in HEX_VERTICES[h]:
            b = g.buildings.get(v)
            if not b:
                continue
            q, kind = b
            if q == p:
                own = True
            else:
                score += (2 if kind == "city" else 1) * (1 + 0.3 * g.public_vp(q))
        score *= PIPS[num] if num else 0.2
        victims = g.robber_victims(p, h)
        if victims:
            score += 2
        if own:
            score -= 100
        victim = None
        if victims:
            victim = max(victims, key=lambda q: (g.public_vp(q), g.players[q].num_cards))
        ranked.append((score, h, victim))
    ranked.sort(key=lambda t: -t[0])
    return ranked


def robber_choice(g: Game, p: int):
    _, h, victim = robber_ranking(g, p)[0]
    return h, victim


def robber_hurts_me(g: Game, p: int) -> bool:
    if g.board.numbers[g.robber] is None:
        return False
    return any(g.buildings.get(v, (None,))[0] == p for v in HEX_VERTICES[g.robber])


def choose_goal(g: Game, p: int):
    """Pick what to save for: (build kind, extra info)."""
    pl = g.players[p]
    hand = pl.hand
    options = []
    if pl.pieces["city"] and g.legal_cities(p):
        options.append(("city", 3.0, None))
    spots = g.legal_settlements(p) if pl.pieces["settlement"] else []
    if spots:
        options.append(("settlement", 3.3, None))
    elif pl.pieces["settlement"] and pl.pieces["road"]:
        target = road_target(g, p)
        if target:
            options.append(("road", 2.2, target))
    if g.dev_deck:
        options.append(("dev", 1.3, None))
    if not options:
        return None
    best = max(options, key=lambda o: o[1] / (1 + sum(missing(hand, COSTS[o[0]]).values())))
    return best[0], best[2]


# ---------------------------------------------------------------- phase handlers
def _setup(g: Game, p: int):
    if g.setup_expect == "settlement":
        spots = g.legal_settlements(p, setup=True)
        v = max(spots, key=lambda v: vertex_value(g, p, v))
        g.place_settlement_setup(p, v)
        return
    v0 = g.setup_vertex
    best, best_score = None, -1.0
    for e in VERTEX_EDGES[v0]:
        if e in g.roads:
            continue
        a, b = EDGES[e]
        u = b if a == v0 else a
        score = 0.0
        for w in VERTEX_NEIGHBORS[u]:
            if w != v0 and g.can_place_settlement(p, w, setup=True) is None:
                score = max(score, vertex_value(g, p, w))
        if score > best_score:
            best, best_score = e, score
    g.place_road_setup(p, best)


def discard_choice(g: Game, p: int) -> dict:
    hand = dict(g.players[p].hand)
    out = {r: 0 for r in RESOURCES}
    for _ in range(g.discards[p]):
        choices = [r for r in RESOURCES if hand[r] > 0]

        def loss(r):
            hand[r] -= 1
            u = hand_utility(g, p, hand)
            hand[r] += 1
            return -u, -hand[r]

        r = min(choices, key=loss)
        hand[r] -= 1
        out[r] += 1
    return out


def would_accept(g: Game, p: int) -> bool:
    t = g.trade
    proposer = t["from"]
    hand = dict(g.players[p].hand)
    after = dict(hand)
    for r, k in t["get"].items():
        after[r] -= k
    for r, k in t["give"].items():
        after[r] += k
    return (g.public_vp(proposer) < 8
            and sum(t["give"].values()) >= sum(t["get"].values())
            and hand_utility(g, p, after) > hand_utility(g, p, hand) + 0.02)


def _try_dev(g: Game, p: int, goal) -> bool:
    playable = g.playable_devs(p)
    if not playable:
        return False
    pl = g.players[p]
    if "knight" in playable:
        others = max((g.players[q].knights for q in range(g.n) if q != p), default=0)
        gains_army = (g.largest_army != p and pl.knights + 1 >= 3 and pl.knights + 1 > others)
        if robber_hurts_me(g, p) or gains_army:
            h, victim = robber_choice(g, p)
            g.play_knight(p, h, victim)
            return True
    if g.phase != "main":
        return False
    if "road_building" in playable and pl.pieces["road"] >= 2 and pl.pieces["settlement"] \
            and road_target(g, p):
        g.play_road_building(p)
        return True
    if "year_of_plenty" in playable and goal:
        need = missing(pl.hand, COSTS[goal[0]])
        want = [r for r, k in need.items() for _ in range(k)][:2]
        if want:
            while len(want) < 2:
                want.append(min(RESOURCES, key=lambda r: pl.hand[r]))
            if all(g.bank[r] >= want.count(r) for r in want):
                g.play_year_of_plenty(p, want[0], want[1])
                return True
    if "monopoly" in playable:
        def estimate(r):
            total = 0.0
            for q in range(g.n):
                if q == p:
                    continue
                prod = g.production(q)
                share = prod[r] / max(1, sum(prod.values()))
                total += g.players[q].num_cards * share
            return total
        r = max(RESOURCES, key=estimate)
        if estimate(r) >= 2.5:
            g.play_monopoly(p, r)
            return True
    return False


def _maritime(g: Game, p: int, goal_cost: dict) -> bool:
    hand = g.players[p].hand
    need = missing(hand, goal_cost)
    if not need:
        return False
    surplus = {r: hand[r] - goal_cost.get(r, 0) for r in RESOURCES}
    options = []
    for give in RESOURCES:
        ratio = g.trade_ratio(p, give)
        if surplus[give] >= ratio:
            options.append((surplus[give] - ratio, -ratio, give))
    if not options:
        return False
    _, _, give = max(options)
    get = max(need, key=lambda r: (need[r], -g.players[p].hand[r]))
    if g.bank[get] == 0:
        return False
    g.maritime_trade(p, give, get)
    return True


def _offer(g: Game, p: int, goal_cost: dict) -> bool:
    hand = g.players[p].hand
    need = missing(hand, goal_cost)
    if g.trades_proposed or sum(need.values()) != 1:
        return False
    surplus = {r: hand[r] - goal_cost.get(r, 0) for r in RESOURCES}
    give = max(RESOURCES, key=lambda r: surplus[r])
    if surplus[give] < 1:
        return False
    get = next(iter(need))
    g.propose_trade(p, {give: 1}, {get: 1})
    return True


def _main(g: Game, p: int):
    pl = g.players[p]
    turn, count = g.meta.get("bot_actions", [None, 0])
    count = count + 1 if turn == g.turn else 1
    g.meta["bot_actions"] = [g.turn, count]
    if count > MAX_ACTIONS_PER_TURN:
        g.end_turn(p)
        return

    goal = choose_goal(g, p)
    if _try_dev(g, p, goal):
        return
    if pl.has(COSTS["city"]) and pl.pieces["city"]:
        cities = g.legal_cities(p)
        if cities:
            g.build_city(p, max(cities, key=lambda v: sum(
                PIPS.get(g.board.numbers[h], 0) for h in VERTEX_HEXES[v])))
            return
    if pl.has(COSTS["settlement"]) and pl.pieces["settlement"]:
        spots = g.legal_settlements(p)
        if spots:
            g.build_settlement(p, max(spots, key=lambda v: vertex_value(g, p, v)))
            return
    if goal and goal[0] == "road" and pl.has(COSTS["road"]):
        g.build_road(p, goal[1][1])
        return
    if goal and goal[0] == "dev" and pl.has(COSTS["dev"]):
        g.buy_dev(p)
        return
    if goal:
        cost = COSTS[goal[0]]
        if _maritime(g, p, cost):
            return
        if goal[0] in ("settlement", "city") and _offer(g, p, cost):
            return
    # Too many cards in hand invites the robber: spend some.
    if pl.num_cards > 7:
        if pl.has(COSTS["dev"]) and g.dev_deck:
            g.buy_dev(p)
            return
        if pl.has(COSTS["road"]) and pl.pieces["road"]:
            e = best_road_for_length(g, p)
            if e is not None:
                g.build_road(p, e)
                return
    g.end_turn(p)


def _road_building(g: Game, p: int):
    target = road_target(g, p)
    e = target[1] if target else best_road_for_length(g, p)
    if e is None:
        g.end_road_building(p)
    else:
        g.build_road(p, e)


def act(g: Game, p: int):
    """Make one move for bot player p."""
    ph = g.phase
    if ph == "setup":
        _setup(g, p)
    elif ph == "roll":
        if not (robber_hurts_me(g, p) and _try_dev(g, p, None)):
            g.roll(p)
    elif ph == "discard":
        g.discard(p, discard_choice(g, p))
    elif ph == "robber":
        h, victim = robber_choice(g, p)
        g.move_robber(p, h, victim)
    elif ph == "main":
        _main(g, p)
    elif ph == "road_building":
        _road_building(g, p)
    elif ph == "trade":
        if g.trade["stage"] == "respond":
            g.respond_trade(p, would_accept(g, p))
        else:
            acc = g.trade_acceptors()
            g.confirm_trade(p, min(acc, key=g.public_vp))
    else:
        raise IllegalAction(f"bot cannot act in phase {ph}")


def run_bots(g: Game, max_steps: int = 100000) -> int:
    """Let bots move until a human must act or the game ends."""
    steps = 0
    while steps < max_steps:
        bots = [q for q in g.pending() if g.players[q].bot]
        if not bots:
            break
        act(g, bots[0])
        steps += 1
    return steps

import json
import unittest

from catan import bots
from catan.board import (EDGES, HEX_VERTICES, NUM_EDGES, NUM_HEXES, NUM_VERTICES, PORT_EDGES,
                         VERTEX_HEXES, Board, edge_between)
from catan.game import Game, IllegalAction

NAMES = ["Red", "Blue", "Orange", "White"]


def blank_game(n=4, seed=1):
    """A beginner-board game already in player 0's main phase, board empty."""
    g = Game(NAMES[:n], seed=seed, layout="beginner", first=0)
    g.setup_index = len(g.setup_queue)
    g.phase, g.turn, g.rolled, g.current = "main", 1, True, 0
    return g


def give(g, p, **cards):
    for r, k in cards.items():
        g.players[p].hand[r] += k
        g.bank[r] -= k


def settle(g, p, v, kind="settlement"):
    g.buildings[v] = (p, kind)
    g.players[p].pieces[kind] -= 1


def road(g, p, *path):
    for a, b in zip(path, path[1:]):
        g.roads[edge_between(a, b)] = p
        g.players[p].pieces["road"] -= 1


def hex_with(g, resource, number=None):
    return next(h for h in range(NUM_HEXES) if g.board.terrain[h] == resource
                and (number is None or g.board.numbers[h] == number))


def free_corner(g, h, avoid=()):
    return next(v for v in HEX_VERTICES[h] if v not in avoid)


class BoardTests(unittest.TestCase):
    def test_geometry(self):
        self.assertEqual((NUM_HEXES, NUM_VERTICES, NUM_EDGES), (19, 54, 72))
        coastal = [e for e in EDGES
                   if sum(1 for hv in HEX_VERTICES if e[0] in hv and e[1] in hv) == 1]
        self.assertEqual(len(coastal), 30)
        self.assertTrue(all(e in coastal for e in PORT_EDGES))
        self.assertEqual(len({v for e in PORT_EDGES for v in e}), 18)

    def test_random_layouts_are_valid(self):
        import random
        for seed in range(100):
            b = Board.random(random.Random(seed))
            self.assertEqual(sorted(x for x in b.numbers if x),
                             [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12])
            self.assertIsNone(b.numbers[b.desert])
            self.assertFalse(b.red_numbers_adjacent())
            self.assertEqual(sorted(b.port_types).count("any"), 4)

    def test_beginner_board(self):
        b = Board.beginner()
        self.assertEqual(b.terrain.count("lumber"), 4)
        self.assertEqual(b.terrain.count("ore"), 3)
        self.assertFalse(b.red_numbers_adjacent())


class SetupTests(unittest.TestCase):
    def test_snake_order_and_starting_resources(self):
        g = Game(NAMES, seed=5, layout="beginner", first=2)
        self.assertEqual(g.setup_queue, [2, 3, 0, 1, 1, 0, 3, 2])
        order = []
        while g.phase == "setup":
            p = g.current
            order.append(p)
            bots.act(g, p)  # settlement
            bots.act(g, p)  # road
        self.assertEqual(order, [2, 3, 0, 1, 1, 0, 3, 2])
        self.assertEqual((g.phase, g.current, g.turn), ("roll", 2, 1))
        for p in range(4):
            self.assertEqual(g.players[p].pieces, {"road": 13, "settlement": 3, "city": 4})
            self.assertEqual(g.public_vp(p), 2)
        # Each player received one card per producing hex around the 2nd settlement.
        self.assertEqual(sum(pl.num_cards for pl in g.players) + sum(g.bank.values()), 95)
        self.assertGreater(sum(pl.num_cards for pl in g.players), 0)

    def test_setup_rules(self):
        g = Game(NAMES, seed=5, layout="beginner", first=0)
        g.place_settlement_setup(0, 8)
        with self.assertRaises(IllegalAction):        # road first
            g.place_settlement_setup(0, 20)
        with self.assertRaises(IllegalAction):        # must touch new settlement
            g.place_road_setup(0, edge_between(13, 9))
        g.place_road_setup(0, edge_between(8, 13))
        with self.assertRaises(IllegalAction):        # distance rule
            g.place_settlement_setup(1, 13)
        with self.assertRaises(IllegalAction):        # not your turn
            g.place_settlement_setup(2, 20)
        g.place_settlement_setup(1, 20)
        self.assertEqual(g.players[0].num_cards, 0)   # first settlement gives nothing


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.g = g = blank_game()
        g.phase, g.rolled = "roll", False
        self.h = hex_with(g, "grain", 9)

    def test_settlement_and_city_production(self):
        g, h = self.g, self.h
        a, b = HEX_VERTICES[h][0], HEX_VERTICES[h][3]
        settle(g, 1, a)
        settle(g, 2, b, "city")
        g.roll(0, (4, 5))
        self.assertEqual(g.players[1].hand["grain"], 1)
        self.assertEqual(g.players[2].hand["grain"], 2)
        self.assertEqual(g.phase, "main")

    def test_robber_blocks(self):
        g, h = self.g, self.h
        settle(g, 1, HEX_VERTICES[h][0])
        g.robber = h
        g.roll(0, (4, 5))
        self.assertEqual(g.players[1].hand["grain"], 0)

    def test_bank_shortage(self):
        g, h = self.g, self.h
        settle(g, 1, HEX_VERTICES[h][0])
        settle(g, 2, HEX_VERTICES[h][3])
        g.bank["grain"] = 1
        g.roll(0, (4, 5))
        self.assertEqual(g.players[1].hand["grain"] + g.players[2].hand["grain"], 0)
        # A single claimant receives what is left.
        g2 = blank_game()
        g2.phase, g2.rolled = "roll", False
        settle(g2, 1, HEX_VERTICES[h][0], "city")
        g2.bank["grain"] = 1
        g2.roll(0, (4, 5))
        self.assertEqual(g2.players[1].hand["grain"], 1)


class RobberTests(unittest.TestCase):
    def test_seven_discard_and_steal(self):
        g = blank_game()
        g.phase, g.rolled = "roll", False
        give(g, 0, brick=8)
        give(g, 1, ore=5, wool=4)
        give(g, 2, grain=7)
        target = hex_with(g, "ore")
        settle(g, 1, HEX_VERTICES[target][0])
        g.roll(0, (3, 4))
        self.assertEqual(g.phase, "discard")
        self.assertEqual(g.discards, {0: 4, 1: 4})
        self.assertEqual(set(g.pending()), {0, 1})
        with self.assertRaises(IllegalAction):
            g.discard(1, {"ore": 3})
        with self.assertRaises(IllegalAction):
            g.move_robber(0, target)
        g.discard(1, {"ore": 2, "wool": 2})
        g.discard(0, {"brick": 4})
        self.assertEqual(g.phase, "robber")
        with self.assertRaises(IllegalAction):
            g.move_robber(0, g.robber)
        with self.assertRaises(IllegalAction):
            g.move_robber(0, target, 2)      # Orange has no building there
        g.move_robber(0, target)             # only one victim: chosen automatically
        self.assertEqual(g.robber, target)
        self.assertEqual(g.players[1].num_cards, 4)
        self.assertEqual(g.players[0].num_cards, 5)
        self.assertEqual(g.phase, "main")

    def test_must_choose_between_victims(self):
        g = blank_game()
        g.phase = "robber"
        g.return_phase = "main"
        h = hex_with(g, "ore")
        settle(g, 1, HEX_VERTICES[h][0])
        settle(g, 2, HEX_VERTICES[h][3])
        give(g, 1, ore=1)
        give(g, 2, wool=1)
        with self.assertRaises(IllegalAction):
            g.move_robber(0, h)
        g.move_robber(0, h, 2)
        self.assertEqual(g.players[0].hand["wool"], 1)


class BuildTests(unittest.TestCase):
    def test_road_connectivity_and_opponent_block(self):
        g = blank_game()
        settle(g, 0, 8)
        give(g, 0, brick=5, lumber=5)
        with self.assertRaises(IllegalAction):
            g.build_road(0, edge_between(13, 9))      # not connected
        g.build_road(0, edge_between(8, 13))
        settle(g, 1, 9)                               # opponent on the far end... of 13-9
        g.build_road(0, edge_between(13, 9))          # touching 13 (ours) is fine
        with self.assertRaises(IllegalAction):
            g.build_road(0, edge_between(9, 14))      # can't continue through 9
        with self.assertRaises(IllegalAction):
            g.build_road(0, edge_between(8, 13))      # occupied path
        self.assertEqual(g.players[0].hand, {"brick": 3, "lumber": 3, "wool": 0, "grain": 0, "ore": 0})

    def test_settlement_rules_and_city(self):
        g = blank_game()
        settle(g, 0, 8)
        road(g, 0, 8, 13, 18)
        give(g, 0, brick=2, lumber=2, wool=2, grain=4, ore=3)
        with self.assertRaises(IllegalAction):
            g.build_settlement(0, 13)                 # distance rule
        with self.assertRaises(IllegalAction):
            g.build_settlement(0, 30)                 # not on our road
        g.build_settlement(0, 18)
        self.assertEqual(g.public_vp(0), 2)
        with self.assertRaises(IllegalAction):
            g.build_city(0, 13)
        g.build_city(0, 18)
        self.assertEqual(g.public_vp(0), 3)
        self.assertEqual(g.players[0].pieces["settlement"], 4)
        self.assertEqual(g.players[0].pieces["city"], 3)
        with self.assertRaises(IllegalAction):
            g.build_city(0, 8)                        # can't afford a second

    def test_piece_limits(self):
        g = blank_game()
        settle(g, 0, 8)
        g.players[0].pieces["road"] = 0
        give(g, 0, brick=1, lumber=1)
        with self.assertRaises(IllegalAction):
            g.build_road(0, edge_between(8, 13))

    def test_not_your_turn_and_must_roll(self):
        g = blank_game()
        settle(g, 1, 8)
        give(g, 1, brick=1, lumber=1)
        with self.assertRaises(IllegalAction):
            g.build_road(1, edge_between(8, 13))
        g.phase, g.rolled = "roll", False
        settle(g, 0, 20)
        give(g, 0, brick=1, lumber=1)
        with self.assertRaises(IllegalAction):
            g.build_road(0, edge_between(20, 25))


class LongestRoadTests(unittest.TestCase):
    PATH = (0, 4, 8, 13, 9, 14, 19, 24)

    def test_award_tie_and_takeover(self):
        g = blank_game()
        settle(g, 0, 0)
        road(g, 0, *self.PATH[:5])                  # 4 segments
        give(g, 0, brick=5, lumber=5)
        g._update_longest_road()
        self.assertIsNone(g.longest_road)
        g.build_road(0, edge_between(9, 14))        # 5 segments
        self.assertEqual(g.longest_road, 0)
        self.assertEqual(g.public_vp(0), 3)
        # Blue ties at 5: Red keeps it. Blue reaches 6: Blue takes it.
        settle(g, 1, 53)
        road(g, 1, 53, 50, 46, 42, 37)
        g.current = 1
        give(g, 1, brick=5, lumber=5)
        g.build_road(1, edge_between(37, 32))
        self.assertEqual(g.road_length(1), 5)
        self.assertEqual(g.longest_road, 0)
        g.build_road(1, edge_between(32, 26))
        self.assertEqual(g.longest_road, 1)

    def test_broken_road_set_aside(self):
        g = blank_game()
        settle(g, 0, 0)
        road(g, 0, *self.PATH[:7])                  # 6 segments, 0..19
        g._update_longest_road()
        self.assertEqual(g.longest_road, 0)
        # Blue settles on 13, splitting Red's road into 3 + 3.
        road(g, 1, 18, 13)
        g.current = 1
        give(g, 1, brick=1, lumber=1, wool=1, grain=1)
        g.build_settlement(1, 13)
        self.assertEqual(g.road_length(0), 3)
        self.assertIsNone(g.longest_road)           # nobody has 5+: set aside

    def test_broken_road_goes_to_unique_longest(self):
        g = blank_game()
        settle(g, 0, 0)
        road(g, 0, *self.PATH[:7])
        settle(g, 2, 53)
        road(g, 2, 53, 50, 46, 42, 37, 32)          # Orange: 5
        g._update_longest_road()
        self.assertEqual(g.longest_road, 0)
        road(g, 1, 18, 13)
        g.current = 1
        give(g, 1, brick=1, lumber=1, wool=1, grain=1)
        g.build_settlement(1, 13)
        self.assertEqual(g.longest_road, 2)

    def test_holder_keeps_when_still_tied(self):
        g = blank_game()
        settle(g, 0, 0)
        road(g, 0, *self.PATH)                      # 7 segments
        settle(g, 2, 53)
        road(g, 2, 53, 50, 46, 42, 37, 32)          # Orange: 5
        g._update_longest_road()
        self.assertEqual(g.longest_road, 0)
        # Blue settles on 14: Red is left with 5 + 2, tied with Orange -> Red keeps it.
        road(g, 1, 10, 14)
        g.current = 1
        give(g, 1, brick=1, lumber=1, wool=1, grain=1)
        g.build_settlement(1, 14)
        self.assertEqual(g.road_length(0), 5)
        self.assertEqual(g.longest_road, 0)


class DevCardTests(unittest.TestCase):
    def test_cannot_play_card_bought_this_turn(self):
        g = blank_game()
        give(g, 0, wool=2, grain=2, ore=2)
        g.dev_deck.append("knight")
        g.buy_dev(0)
        self.assertEqual(g.players[0].devs, ["knight"])
        with self.assertRaises(IllegalAction):
            g.play_knight(0)
        self.assertEqual(g.playable_devs(0), [])
        g.end_turn(0)
        for p in (1, 2, 3):
            g.roll(p, (2, 2))
            g.end_turn(p)
        self.assertEqual(g.playable_devs(0), ["knight"])

    def test_knight_before_roll_and_one_card_per_turn(self):
        g = blank_game()
        g.phase, g.rolled = "roll", False
        g.players[0].devs = ["knight", "monopoly"]
        h = hex_with(g, "ore")
        g.play_knight(0, h)
        self.assertEqual((g.phase, g.robber, g.players[0].knights), ("roll", h, 1))
        g.roll(0, (2, 2))
        with self.assertRaises(IllegalAction):
            g.play_monopoly(0, "ore")

    def test_largest_army(self):
        g = blank_game()
        g.players[0].knights = 2
        g.players[0].devs = ["knight"]
        g.play_knight(0, hex_with(g, "ore"))
        self.assertEqual(g.largest_army, 0)
        self.assertEqual(g.public_vp(0), 2)
        g.current = 1
        g.dev_played = False
        g.players[1].knights = 2
        g.players[1].devs = ["knight", "knight"]
        g.play_knight(1, hex_with(g, "wool"))
        self.assertEqual(g.largest_army, 0)          # tie: holder keeps
        g.dev_played = False
        g.play_knight(1, hex_with(g, "brick"))
        self.assertEqual(g.largest_army, 1)

    def test_road_building(self):
        g = blank_game()
        settle(g, 0, 8)
        g.players[0].devs = ["road_building"]
        g.play_road_building(0)
        self.assertEqual((g.phase, g.free_roads), ("road_building", 2))
        with self.assertRaises(IllegalAction):
            g.end_turn(0)
        g.build_road(0, edge_between(8, 13))
        g.build_road(0, edge_between(13, 18))
        self.assertEqual(g.phase, "main")
        self.assertEqual(g.players[0].num_cards, 0)
        self.assertEqual(g.players[0].pieces["road"], 13)

    def test_year_of_plenty_and_monopoly(self):
        g = blank_game()
        g.players[0].devs = ["year_of_plenty", "monopoly"]
        g.play_year_of_plenty(0, "ore", "ore")
        self.assertEqual(g.players[0].hand["ore"], 2)
        self.assertEqual(g.bank["ore"], 17)
        g.dev_played = False
        give(g, 1, wool=3, ore=1)
        give(g, 3, wool=2)
        g.play_monopoly(0, "wool")
        self.assertEqual(g.players[0].hand["wool"], 5)
        self.assertEqual(g.players[1].hand, {"brick": 0, "lumber": 0, "wool": 0, "grain": 0, "ore": 1})

    def test_year_of_plenty_needs_bank_stock(self):
        g = blank_game()
        g.players[0].devs = ["year_of_plenty"]
        g.bank["ore"] = 1
        with self.assertRaises(IllegalAction):
            g.play_year_of_plenty(0, "ore", "ore")
        self.assertEqual(g.players[0].devs, ["year_of_plenty"])

    def test_victory_point_card_wins_immediately(self):
        g = blank_game()
        for v, kind in ((0, "city"), (8, "city"), (20, "city"), (30, "city"), (45, "settlement")):
            settle(g, 0, v, kind)
        self.assertEqual(g.total_vp(0), 9)
        give(g, 0, wool=1, grain=1, ore=1)
        g.dev_deck.append("victory_point")
        g.buy_dev(0)
        self.assertEqual((g.phase, g.winner), ("game_over", 0))


class TradeTests(unittest.TestCase):
    def test_maritime_rates(self):
        g = blank_game()
        give(g, 0, wool=8, brick=4)
        with self.assertRaises(IllegalAction):
            g.maritime_trade(0, "wool", "ore", 3)
        g.maritime_trade(0, "brick", "ore")
        self.assertEqual(g.players[0].hand["ore"], 1)
        any_v, wool_v = PORT_EDGES[0][0], PORT_EDGES[1][0]
        self.assertEqual(g.board.vertex_port[any_v], "any")
        self.assertEqual(g.board.vertex_port[wool_v], "wool")
        settle(g, 0, any_v)
        self.assertEqual(g.trade_ratio(0, "wool"), 3)
        settle(g, 0, wool_v)
        self.assertEqual(g.trade_ratio(0, "wool"), 2)
        self.assertEqual(g.trade_ratio(0, "ore"), 3)
        g.maritime_trade(0, "wool", "grain", 4)
        self.assertEqual(g.players[0].hand["grain"], 4)
        self.assertEqual(g.players[0].hand["wool"], 0)

    def test_domestic_trade_single_acceptor(self):
        g = blank_game()
        give(g, 0, brick=2)
        give(g, 1, ore=1)
        give(g, 2, ore=1)
        g.propose_trade(0, {"brick": 2}, {"ore": 1})
        self.assertEqual(g.pending(), [1, 2])       # White can't pay: auto-declines
        g.respond_trade(1, True)
        g.respond_trade(2, False)
        self.assertEqual(g.phase, "main")
        self.assertEqual(g.players[0].hand["ore"], 1)
        self.assertEqual(g.players[1].hand["brick"], 2)

    def test_domestic_trade_choose_partner(self):
        g = blank_game()
        give(g, 0, brick=1)
        give(g, 1, ore=1)
        give(g, 3, ore=1)
        g.propose_trade(0, {"brick": 1}, {"ore": 1})
        g.respond_trade(1, True)
        g.respond_trade(3, True)
        self.assertEqual(g.trade["stage"], "choose")
        self.assertEqual(g.pending(), [0])
        with self.assertRaises(IllegalAction):
            g.confirm_trade(0, 2)
        g.confirm_trade(0, 3)
        self.assertEqual(g.players[3].hand["brick"], 1)

    def test_invalid_offers(self):
        g = blank_game()
        give(g, 0, brick=1)
        with self.assertRaises(IllegalAction):
            g.propose_trade(0, {"brick": 1}, {})    # no gifts
        with self.assertRaises(IllegalAction):
            g.propose_trade(0, {"ore": 1}, {"brick": 1})  # don't have it
        with self.assertRaises(IllegalAction):
            g.propose_trade(0, {"brick": 1}, {"brick": 1})
        g.phase, g.rolled = "roll", False
        with self.assertRaises(IllegalAction):
            g.propose_trade(0, {"brick": 1}, {"ore": 1})  # must roll first


class WholeGameTests(unittest.TestCase):
    def test_bot_games_finish_and_conserve_cards(self):
        for seed in range(30):
            g = Game(NAMES, bots=[True] * 4, seed=seed)
            bots.run_bots(g)
            self.assertEqual(g.phase, "game_over", seed)
            self.assertGreaterEqual(g.total_vp(g.winner), 10)
            for r in g.bank:
                self.assertEqual(g.bank[r] + sum(p.hand[r] for p in g.players), 19)
            dev_total = len(g.dev_deck) + sum(len(p.devs) for p in g.players)
            played = sum(p.knights for p in g.players)
            self.assertLessEqual(dev_total + played, 25)

    def test_three_player_game(self):
        g = Game(NAMES[:3], bots=[True] * 3, seed=11)
        bots.run_bots(g)
        self.assertEqual(g.phase, "game_over")

    def test_save_load_is_deterministic(self):
        g = Game(NAMES, bots=[True] * 4, seed=42)
        bots.run_bots(g, max_steps=400)
        clone = Game.from_dict(json.loads(json.dumps(g.to_dict())))
        bots.run_bots(g)
        bots.run_bots(clone)
        self.assertEqual(g.log, clone.log)
        self.assertEqual(g.winner, clone.winner)


if __name__ == "__main__":
    unittest.main()

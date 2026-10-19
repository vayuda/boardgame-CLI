# Catan CLI

The classic *Settlers of Catan* base game, for 2 to 4 players. You can play it in a terminal
or in your browser, against:

- built-in bots,
- other people on the same machine (hot-seat),
- **Claude**, with each Claude agent playing a seat through the CLI.

It needs only Python 3.10 or newer. There are no dependencies.

```
python3 -m catan                 # 1 human vs 3 bots, in the terminal
pip install . && catan           # same, as a `catan` command
```

## Play against Claude in your browser

Open this repository in [Claude Code](https://code.claude.com) on your own computer and run:

```
/catan-game 1          # a 1v1 against Claude
/catan-game 3 Ann      # you (Ann) against three Claude agents
```

The skill does three things:

1. creates the game,
2. starts the browser UI (`catan serve`, opening http://localhost:8765/),
3. starts one `catan-player` subagent in the background for each Claude seat.

You click your moves on the board. The agents' moves show up on their own, and you can
keep chatting with Claude in the meantime.

To set it up by hand instead:

```sh
python3 -m catan new --players 2 --humans 1 --agents 1 --names Ann,Claude
python3 -m catan serve                # browser UI for your seat (red)
# then ask Claude Code: "use the catan-player agent to play blue"
```

**The browser UI** has:

- the island board, with every spot you can use glowing (settlements, roads, cities,
  robber hexes);
- an action panel for the current step (roll, discard picker, bank trade, trade offers,
  each development card, accepting or declining offers);
- your hand, the players' table and the game log;
- a command line that accepts every CLI command.

**Agent seats** are moved by outside programs through two commands. Any agent framework
can use them, not just Claude Code:

| Command | What it does |
|---|---|
| `catan wait --as blue` | Blocks until Blue has a decision to make, then prints what happened, Blue's hand, and a `Next:` line. Exit 0 means your move, 2 means it timed out (run it again), and 3 means the game is over. |
| `catan do --as blue 'r 12 13; s 14; e'` | Makes Blue's moves. |

Every view is filtered to that seat, so agents never see each other's cards. The save
file is locked during each change, so the browser and several agents can share one game
safely.

## The board

```
              3:1                   2:1 wool
                    0           1           2
                 /     \     /     \     /     \
              3     A     4w    B     5     C     6
              |   lumber  |    ore    |   grain   |
              |     8     |     4     |     11    |
              |   •••••   |    •••    |     ••    |
              7           8           9          10   3:1
           /     \    /o/   \o\    /     \     /     \
       11     D    12o    E    13     F    14     G    15
   2:1  |   brick   |    wool   |    wool   |    ore    |
 brick  |     10    |     11    |     3     |     12    |
```

* **Intersections** are numbered 0–53, top to bottom. You build on them by number:
  `s 14`, `c 14`.
* **Roads** are named by the two intersections they join: `r 12 13` or `r 12-13`.
* **Hexes** are lettered A–S. You move the robber by letter: `rob K`.
* **Pieces** show the owner's colour letter. `12o` is an Orange settlement and `41W` is a
  White city (uppercase means city). A road shows its owner's letter, for example
  `/o/` or `|o|`.
* **Dots** under a number show how often it rolls; 6 and 8 are red. Cyan intersection
  numbers touch a harbour.

With colour on (the default in a terminal), each player's pieces get a coloured
background.

## Playing (interactive)

Each prompt shows your hand and what the game expects from you:

```
Red 3VP [B0 L1 W2 G0 O0] dev:K main>
```

| Command | What it does |
|---|---|
| `r` | roll. The game rolls for you if you hold no card you could play first. |
| `r 12 13` · `r 12-13` · `r 13` | build a road (`r 13` works when only one legal road touches 13) |
| `s 14` · `c 14` · `dev` | settlement, city, buy a development card |
| `bank w o [n]` · `bank 4w o` | trade with the bank at your best rate (4:1, 3:1 or 2:1 harbour) |
| `offer 2b for o [to blue]` | offer a trade to everyone, or to one player |
| `y` / `n` | accept or decline an offer made to you |
| `pick blue` / `cancel` | choose a partner when several players accept, or withdraw your offer |
| `k F [blue]` | play a Knight: robber to hex F, rob Blue |
| `rb [12-13 13-14]` | play Road Building, then place 2 free roads (`stop` to end early) |
| `yop o g` · `mono w` | play Year of Plenty or Monopoly |
| `rob F [blue]` | move the robber after a 7 (you only name a victim when there's a choice) |
| `d 2o w` | discard after a 7 |
| `e` | end your turn |
| `?` | legal moves and suggestions, depending on the phase |
| `m` · `st` · `log [n]` · `ports` | map, status table, recent events, harbour list |
| `info 23` · `info K` | details on an intersection or a hex |
| `set autoroll / offers / privacy / color on\|off` | game settings |
| `save [file]` · `q` | save, or quit (the game autosaves) |
| `h` | full help |

Shorthand:

* **Cards:** `b`=brick, `l`=lumber (wood), `w`=wool (sheep), `g`=grain (wheat),
  `o`=ore. You can write them as `2b w`, `bbw`, `2 brick wool`, or `wood+wheat`.
* **Players:** by name, colour, or seat number (`blue`, `b`, `2`).
* **Setup:** type just the intersection number for your settlement (`23`), then just the
  far end of its road (`24`).
* **Chaining:** separate commands with `;`, for example `r 12 13; s 14; e`. A chain stops
  at the first error, or when the turn moves on.

Settings:

* `set offers off` declines the bots' trade offers without asking you.
* `set autoroll off` makes you type `r` yourself.
* `set privacy` applies to hot-seat games. With it on, the screen clears between
  players and the next player presses Enter before seeing their hand.

### Options

```
catan play [--humans N] [--players 3|4] [--names Ann,Bob] [--seed S]
           [--layout random|beginner] [--first N] [--save FILE | --no-save]
           [--resume | --load FILE] [--no-color] [--ascii]
```

* `--humans 4` sets up a 4-person hot-seat game. `--humans 0` gives a bot-only game.
* `--agents N` (with `catan new`) gives the N seats after the humans to outside agents,
  which play through `catan wait`/`catan do`. Any remaining seats are bots.
* `--players 2` plays a 1v1 under the normal rules. The official two-player variant, with
  neutral players, isn't implemented.
* `--layout beginner` uses the fixed starter board from the rulebook. `random` is the
  standard variable setup: shuffled terrain and harbours, number tokens placed in the
  official spiral, and no 6 or 8 next to each other.
* The game autosaves to `catan_save.json`. If an unfinished save exists, the game offers
  to resume it.

## Playing one command at a time (scripts and AI agents)

The same commands also work as single shell calls against a save file. Each call:

1. loads the game,
2. runs your commands,
3. lets the bots play until you're needed again,
4. prints what happened since your last call,
5. saves.

```sh
catan new --seed 7                  # new game in catan_save.json; prints board + "Next:"
catan do '?'                        # best settlement spots, ranked
catan do '24; 30'                   # settlement on 24, road 24-30 (bots then place theirs)
catan do 'r 30 35; s 35; e'         # a whole turn in one call
catan show                          # board, status table and what's expected next
catan show --no-board --log 20      # just recent events and the next step
catan do --as blue 'y'              # hot-seat: act as a particular player
```

Output is plain text and has no colour when piped. Each call ends with your prompt line
and a `Next:` hint, for example:

```
  Red built a road on 8-13.
  Red ended the turn.
== Turn 2: Blue ==
  Blue rolled 10 (5+5).
    Red +1 brick; Blue +1 brick; Orange +1 lumber
  Blue built a road on 34-39.
  Blue ended the turn.
...
== Turn 5: Red ==
  Red rolled 9 (4+5).
    Red +1 lumber +1 wool; White +1 lumber
Red 2VP [B1 L3 W1 G0 O0] main
Next: Red: build, trade, play a card, or e to end the turn. ? lists options.
```

* When something fails, the error line starts with `!` and the exit status is 1.
* The board prints automatically at the start of each of your turns. Use `--board` to
  always print it, or `--no-board` to never print it.
* `catan show --as blue --json` prints the same seat-filtered state the browser uses.
* `catan sim -n 100 --seed 1` plays bot-only games and reports the results.
  Add `-v` to print a game's full log.

## Rules implemented

This is the full base game:

* **Board:** 19 hexes (4 forest, 4 pasture, 4 fields, 3 hills, 3 mountains, 1 desert),
  18 number tokens, and 9 harbours (four 3:1, five 2:1) at the standard frame positions.
* **Setup:** snake order (1-2-3-4-4-3-2-1). The distance rule applies, each road touches
  the settlement just placed, and the second settlement collects starting resources.
* **Production:** settlements produce 1, cities produce 2, and the robber blocks its
  hex. The bank holds 19 of each resource. When the bank can't pay everyone who is owed
  a resource, nobody gets it, unless only one player is owed it; that player gets
  whatever is left.
* **Rolling a 7:** everyone holding more than 7 cards discards half, rounded down. You
  must move the robber to a different hex, then steal one random card from a player
  with a building there.
* **Building:** roads must connect to your network and can't pass through an opponent's
  building. Settlements must be on your road and obey the distance rule. Pieces are
  limited to 15 roads, 5 settlements and 4 cities, and a city returns its settlement to
  your supply.
* **Development cards:** 25 cards (14 Knight, 2 Road Building, 2 Year of Plenty,
  2 Monopoly, 5 Victory Point). You play at most one per turn. It can be played at any
  point in your turn, including before you roll, but not on the turn you bought it.
  Victory Point cards stay hidden and count automatically.
* **Longest Road:** you need at least 5 segments. Opponents' buildings break a road. The
  holder keeps the card when tied. If the holder's road is broken, the card goes to the
  player who is now uniquely longest. If two or more players tie for longest, or nobody
  has 5, the card is set aside. These follow the official FAQ.
* **Largest Army:** you need at least 3 knights, and you must have strictly more knights
  than the holder to take it.
* **Trading:** you can trade only during your own turn and after you roll. Bank trades
  are 4:1, or 3:1 and 2:1 with harbours. Player trades must have cards on both sides
  (no gifts, no swapping a resource for itself). When several players accept, the
  offerer chooses.
* **Winning:** you win as soon as you reach 10 VP on your turn.

Places where the game decides something for you:

* A player who can't pay for an offer declines it automatically.
* The beginner board's terrain and numbers follow the rulebook. Its harbour types come
  from memory of the rulebook picture, because I couldn't check them against a copy.

## Bots

The bots use only what a real player can see: the board, the public counts, and their
own hand. They:

* rate intersections by pips, resource variety and harbours;
* save for the best build they can reach (city, settlement, a road toward a good spot,
  or a development card);
* trade with the bank when that completes a build;
* offer 1-for-1 trades when one card short of a settlement or city, and accept trades
  that help them, unless the offer comes from someone with 8 or more VP;
* aim the robber at the leader's best hex;
* use all the development cards.

In bot-only games, the game lasts about 65 turns on average.

## Development

```
python3 -m unittest discover -s tests
```

The tests cover:

* the rules (production, bank shortages, robber, building legality, every development
  card, all the Longest Road cases, trades, and winning);
* saving and loading, and that a loaded game continues exactly the same way;
* complete games played only through typed CLI commands, in single-player, hot-seat and
  one-shot modes;
* agent seats:
  * seat privacy;
  * `wait` waking up when the other seat moves;
  * concurrent writers under the lock;
  * the web API, including long polling.

| File | Contents |
|---|---|
| `catan/board.py` | geometry (hexes, numbered intersections, edges, harbours) and layouts |
| `catan/game.py` | rules engine. It is a state machine that never prompts. `pending()` says who must act next. |
| `catan/bots.py` | computer players |
| `catan/render.py` | board and status drawing (ANSI colour optional) |
| `catan/cli.py` | command parser, interactive prompt, and the `new`/`show`/`do`/`wait`/`serve`/`sim` commands |
| `catan/store.py` | shared save file: cross-process lock and atomic writes |
| `catan/api.py` · `catan/server.py` · `catan/web/index.html` | the browser UI: seat-filtered JSON view, a stdlib HTTP server with long polling, and the page |
| `.claude/agents/catan-player.md` · `.claude/skills/catan-game/` | the Claude Code subagent that plays a seat, and the `/catan-game` skill that sets up a game |

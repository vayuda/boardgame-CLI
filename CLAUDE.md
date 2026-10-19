# Catan CLI

This is the classic Settlers of Catan base game for 2–4 players. It is pure Python 3.10+
with no dependencies:

- `catan/game.py` is the rules engine, a prompt-free state machine.
- `catan/bots.py` holds the built-in bots.
- `catan/cli.py` holds the commands.
- `catan/server.py` and `catan/web/index.html` are the browser UI.

Run the tests with `python3 -m unittest discover -s tests`.

## Playing a seat

When asked to play Catan, follow `.claude/agents/catan-player.md`. Each turn has two steps:

1. Run `python3 -m catan wait --as <colour>` with a 600000 ms Bash timeout.
2. Act with `python3 -m catan do --as <colour> '<commands>'`.

To set up a game against the user, use the `catan-game` skill (`/catan-game 1` for a 1v1).
The user plays in the browser via `catan serve`.

Never read `catan_save.json` directly, and never use `--as` for a seat you aren't playing.
Either one reveals hidden cards.

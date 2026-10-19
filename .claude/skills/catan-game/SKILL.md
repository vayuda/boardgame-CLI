---
name: catan-game
description: Start a Settlers of Catan game in the browser where the user plays against 1 to 3 Claude agents, each one playing a seat through the catan CLI. Use when the user wants to play Catan against Claude.
argument-hint: [opponents 1-3] [your name]
allowed-tools: Bash(python3 -m catan *)
---

# Play Catan against Claude

Arguments:

- `$0`: number of Claude opponents, 1 to 3 (default 1).
- `$1`: the user's player name (default `Red`). It must be a single word.

The user sits in seat 1 (red). Claude seats follow: blue, then orange, then white.

1. **Check for an old game.** If `catan_save.json` exists, run
   `python3 -m catan show --no-board --as red`.
   If that game isn't over, ask the user whether to replace it before going on.

2. **Create the game.** Use `--force` only if the user agreed to replace an old game.
   - 1 opponent (a 1v1):
     `python3 -m catan new --force --players 2 --humans 1 --agents 1 --names NAME,Claude --as red --no-board`
   - 2 opponents:
     `python3 -m catan new --force --players 3 --humans 1 --agents 2 --names NAME,Claude-Blue,Claude-Orange --as red --no-board`
   - 3 opponents:
     `python3 -m catan new --force --players 4 --humans 1 --agents 3 --names NAME,Claude-Blue,Claude-Orange,Claude-White --as red --no-board`

3. **Start the browser UI.** Run `python3 -m catan serve --as red` in the background with
   `run_in_background`. It opens http://localhost:8765/. Give the user that link in case no
   tab opened.

4. **Start the opponents.** For each Claude seat (`blue`, then `orange`, then `white`, as many
   as there are opponents), start a `catan-player` subagent in the background with the
   prompt: `Play seat <colour> in the Catan game in catan_save.json until the game ends.`

5. **Tell the user the game is ready.** Say it in two or three lines: they play in the
   browser, the opponents move on their own, and they can ask you here at any time how the
   opponents are doing.

Don't play the Claude seats yourself while the subagents run. Don't read
`catan_save.json`: it holds everyone's hidden cards. If a subagent stops before the game
ends, start a new one for the same seat. It picks up where the game is.

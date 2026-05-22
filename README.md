# Adventure: the player and the harness, swapped

A small experiment built on one observation.

In the 1970s and 80s, text adventure games were marginally popular. Reframe
what was actually happening at the keyboard:

- **The game was the harness.** A parser, a world simulation, a turn loop. It
  emitted natural language and waited for natural language back.
- **The player — the human — was the language model.** They read the prose,
  built a world-model in their head (and on graph paper), reasoned about it,
  and emitted a natural-language command. Turn. Turn. Turn.

Nothing about the *game* needs to change for the modern version. The game is
still the harness. The only thing swapped out is the language model in the
loop: a human in 1977, an LLM agent today.

This repo does exactly that swap, as honestly as possible, and watches what
happens.

## The honesty rule

The whole experiment turns on one rule: **the agent gets the interface the
human got, and not one bit more.**

A human at a teletype had exactly two things: prose printed at them, and a
line of text they could type back. So the agent is given exactly **one tool**:

```
game(command: str) -> str
```

It lowercases the command, splits it into words, and feeds them to the game —
which is *literally the three lines* of Colossal Cave Adventure's own REPL. No
`inventory()` tool. No `look()` tool. No map. No parsed game state. No hint
system.

Every one of those would be a cheat: it would move parser logic or bookkeeping
out of the game and into the tool layer, handing the agent an *easier* game
than the human played. The single string-in / string-out tool is the only one
that keeps the experiment fair.

The system prompt is **blind**, too. It explains how the tool works and
nothing else — it does not name the game, describe the cave, or teach the
parser's command syntax. The game itself offers instructions on turn 1; the
agent decides whether to read them, exactly as a 1977 player decided whether
to read the manual.

## The game

[Colossal Cave Adventure](https://en.wikipedia.org/wiki/Colossal_Cave_Adventure)
(Crowther & Woods, 1977) — the original, the one the whole genre is named
after. Run here via Brandon Rhodes' faithful, public-domain
[`adventure`](https://pypi.org/project/adventure/) Python port. 350 points on
the table. It is genuinely hard.

## The friend in the next chair

There was almost always someone else in the room. A brother on the chair next
to you while you played Space Quest; you, leaning over a friend's shoulder at
his house. They never touched the keyboard — but they read the screen and
called things out. "Try the mailbox." "Don't go in there, it said it's dark."

So there are two models, not one:

- **The player** (Sonnet 4.6) holds the keyboard. It alone has the `game`
  tool. Its thinking streams to your terminal, dimmed.
- **The friend** (Haiku 4.5) is the kibitzer. It sees the screen and the
  commands the player types — but *not* the player's private thinking, and it
  has no tool. Each turn it streams a one- or two-line reaction in its own
  colour. That reaction reaches the player as a clearly-marked aside, to heed
  or wave off.

The honesty rule still holds: the friend reads the screen, not your mind, and
never gets to type. What you watch is the *interplay* — Haiku nudging, Sonnet
deciding. Pass `--solo` to drop the friend and play one-handed.

## Running it

```sh
./setup.sh                                     # venv + deps, writes a .env stub
# then put your key in .env:  ANTHROPIC_API_KEY=sk-...

./adventure.sh --selftest                      # check plumbing, no API needed
./adventure.sh --turns 4                       # short smoke run
./adventure.sh                                 # full run
```

The reasoning streams to your terminal live, turn by turn — the player's
thinking dimmed, the friend's asides in colour, the game's replies boxed. The
display is built with [`rich`](https://github.com/Textualize/rich): it adapts
to your terminal width and falls back to plain text when piped to a file.
Watching the two models think is half the point.

When the game ends — a death, a win, a `quit` — it **restarts from the
beginning with all context kept**, so the agent gets another run at the same
cave carrying everything it learned. The turn cap is the budget across every
attempt. (`--no-restart` ends the run at the first game-over instead.)

Each run writes `logs/transcript-<id>.log` (plain text) and a structured
`logs/turns-<id>.jsonl`. The conversation is append-only, which keeps Anthropic
prompt caching hitting — most of the growing transcript is re-read from cache
each turn rather than reprocessed. Knobs: `--turns`, `--model`, `--effort`,
`--friend-model`, `--solo`, `--no-restart`, `--seed`.

The code is three small modules: `harness.py` (the run loop), `agents.py` (the
Claude calls and prompts), and `tui.py` (the `rich` terminal rendering).

## What to watch for in the transcript

- **Does it recognize the game?** "WELCOME TO ADVENTURE!!" and "standing at
  the end of a road before a small brick building" are a fingerprint. A model
  that has read Crowther & Woods walkthroughs in training may *recognize* the
  game on turn 1 and switch from playing to reciting. The blind prompt avoids
  *priming* that — but it cannot prevent recognition. Watch the thinking
  blocks to see which mode the agent is in.
- **Mapping.** The human had graph paper. The agent has its context window.
  Does it keep a coherent mental map, or get lost in the twisty passages?
- **Memory.** The agent keeps the entire transcript — every room, command,
  and aside — for the whole run; nothing is summarized away. The question is
  whether it *uses* that memory: does it recall a door it couldn't open forty
  turns ago when it finally finds the key?
- **Learning across lives.** When the game restarts, the agent keeps its
  context. Does dying actually teach it anything — does run two go better
  than run one?
- **Parser friction.** A blind agent that skips the instructions has to learn
  the terse two-word command style by trial and error — same as a human who
  threw away the manual.
- **The interplay.** Does the player take the friend's advice, argue with it,
  or quietly ignore it? Does the small fast model (Haiku) spot something the
  player missed — or send it down the wrong corridor? Two models, one cave.

The honest answer to "see how it goes" is: it probably will not win. Adventure
defeats most humans too. The interesting artifact is *how far*, and *how it
reasons* on the way there.

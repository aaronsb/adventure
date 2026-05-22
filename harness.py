#!/usr/bin/env python3
"""
Adventure harness — an honest tool-call interface for an LLM agent to play
Colossal Cave Adventure (Crowther & Woods, 1977).

Premise
-------
In the 1970s and 80s the *player* was the language model: a human read the
game's natural-language output, kept a world-model in their head (and on graph
paper), and emitted natural-language commands. The *game* was the harness — a
parser, a world simulation, a turn loop.

Today the game is still the harness. The player is now an LLM agent (Sonnet),
and — optionally — a second, smaller model (Haiku) plays the friend in the
next chair: it reads the screen and chimes in, but never touches the keyboard.
The only *honest* interface is the one the human had — text in, text out — so
the player gets exactly one tool, `game(command)`, and nothing else.

This file is the orchestration layer. The Anthropic calls and prompts live in
`agents.py`; terminal rendering lives in `tui.py`.

Usage
-----
    ./setup.sh                                  # one-time: venv + deps
    .venv/bin/python harness.py                 # full run
    .venv/bin/python harness.py --turns 4       # short run
    .venv/bin/python harness.py --solo          # no kibitzer friend
    .venv/bin/python harness.py --selftest      # exercise the plumbing, no API
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

from adventure import load_advent_dat
from adventure.game import Game

from agents import (EFFORT, FRIEND_MODEL, FRIEND_NOTE, MODEL, RESTART_NOTE,
                    SYSTEM_PROMPT, TOOL, call_friend, call_model,
                    effective_max_tokens, serialize_block)
from tui import UI

# ── configuration ───────────────────────────────────────────────────────────
MAX_TURNS = 70                # max commands the agent may send (--turns)
GAME_SEED = 1972              # fixed RNG seed so runs are comparable (--seed)
CONTEXT_SOFT_LIMIT = 170_000  # stop the run if the total prompt crosses this


# ── the game, wrapped as text-in / text-out ─────────────────────────────────
class Adventure:
    """The harness's view of the game: text in, text out, nothing else.

    `command()` reproduces exactly what adventure's own REPL does — lowercase
    the line, split it into words, hand them to `do_command` — so the agent
    plays a byte-identical game to the 1977 teletype original (minus only the
    cosmetic 1200-baud output delay).
    """

    def __init__(self, seed: int):
        self._g = Game(seed=seed)
        load_advent_dat(self._g)
        self._g.start()
        self.intro: str = self._g.output

    def command(self, text: str) -> str:
        words = re.findall(r"\w+", (text or "").lower())
        if not words:
            return "(The game shows its prompt again, awaiting a command.)"
        try:
            return self._g.do_command(words)
        except Exception as exc:  # the game should never crash; be safe anyway
            return f"(The game produced an error: {exc!r})"

    @property
    def finished(self) -> bool:
        return self._g.is_finished

    def score(self) -> tuple[int, int]:
        return self._g.compute_score()


# ── environment ─────────────────────────────────────────────────────────────
def load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader: populates os.environ from KEY=value lines without
    overriding variables already set in the real environment."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# ── the run ─────────────────────────────────────────────────────────────────
def run(args) -> None:
    import anthropic

    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set.\n"
                 "  Put it in .env  (ANTHROPIC_API_KEY=sk-...)  or export it.")
    client = anthropic.Anthropic(api_key=api_key, max_retries=5, timeout=180)

    friend_on = not args.solo
    restart_on = not args.no_restart
    system_prompt = (SYSTEM_PROMPT
                     + (FRIEND_NOTE if friend_on else "")
                     + (RESTART_NOTE if restart_on else ""))
    friend_messages: list[dict] = []

    os.makedirs("logs", exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    ui = UI(f"logs/transcript-{run_id}.log")
    turns_path = f"logs/turns-{run_id}.jsonl"
    turns_f = open(turns_path, "w", encoding="utf-8")

    adv = Adventure(seed=args.seed)
    ui.banner(f"ADVENTURE HARNESS — run {run_id}", [
        f"player : {args.model}   (effort: {args.effort}, "
        f"{effective_max_tokens(args.effort):,} max tokens/turn)",
        f"friend : {args.friend_model if friend_on else '(solo run — no friend)'}",
        f"seed {args.seed}   turn cap {args.turns}   "
        f"restart {'on (context kept)' if restart_on else 'off'}",
    ])
    ui.record("game start", adv.intro)

    messages: list[dict] = [{
        "role": "user",
        "content": f"The game has started. It displays:\n\n{adv.intro}",
    }]

    commands_sent = 0
    consecutive_nudges = 0
    games_played = 1
    best_score = 0
    total_cache_read = 0
    last_prompt_tokens = 0
    friend_restart_pending = False
    outcome = "turn cap reached"

    try:
        while commands_sent < args.turns and not adv.finished:
            if consecutive_nudges >= 3:
                outcome = "agent stopped sending commands"
                break

            ui.rule(f"TURN {commands_sent + 1}")
            try:
                resp = call_model(client, args.model, messages, args.effort,
                                  system_prompt, ui, tools=[TOOL])
            except Exception as exc:
                outcome = f"API error: {exc!r}"
                ui.note(f"\n!! {outcome}\n")
                break

            # With caching on, usage.input_tokens is only the *uncached*
            # remainder — sum all three fields for the true prompt size.
            usage = resp.usage
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            prompt_total = usage.input_tokens + cache_read + cache_write
            total_cache_read += cache_read
            last_prompt_tokens = prompt_total

            blocks = [serialize_block(b) for b in resp.content]
            messages.append({"role": "assistant", "content": blocks})
            thinking = "\n".join(b["thinking"] for b in blocks
                                 if b["type"] == "thinking")
            agent_text = "\n".join(b["text"] for b in blocks
                                   if b["type"] == "text")
            tool_uses = [b for b in blocks if b["type"] == "tool_use"]

            # Thinking + text streamed live already; mirror them to the file.
            if thinking:
                ui.record("thinking", thinking)
            if agent_text:
                ui.record("agent", agent_text)

            if not tool_uses:
                consecutive_nudges += 1
                ui.info(f"(no command sent — nudge {consecutive_nudges}/3)")
                messages.append({
                    "role": "user",
                    "content": "(The game is still waiting. Send a command "
                               "with the `game` tool.)",
                })
                continue

            consecutive_nudges = 0
            tu = tool_uses[0]  # disable_parallel_tool_use guarantees at most one
            command = str(tu["input"].get("command", "")).strip()
            response = adv.command(command)
            commands_sent += 1

            ui.command(command)
            ui.game(response)

            # The friend in the next chair reacts to the screen change — their
            # comment streams live and is then handed to the player as a
            # clearly-marked aside (never as the game's own words).
            friend_comment = ""
            if friend_on:
                screen = (f"Your friend typed:   > {command}\n\n"
                          f"The screen now shows:\n{response}")
                if agent_text:
                    screen += f'\n\n(You also heard them say: "{agent_text}")'
                if friend_restart_pending:
                    screen = ("(The game has just started over from the very "
                              "beginning — same cave, another go.)\n\n" + screen)
                    friend_restart_pending = False
                friend_messages.append({"role": "user", "content": screen})
                try:
                    friend_comment = call_friend(client, args.friend_model,
                                                 friend_messages, ui)
                except Exception as exc:
                    ui.note(f"  (friend call failed: {exc!r})")
                friend_messages.append({
                    "role": "assistant",
                    "content": friend_comment or "(says nothing)",
                })
                if friend_comment:
                    ui.record("friend", friend_comment)

            player_turn: list[dict] = [{
                "type": "tool_result",
                "tool_use_id": tu["id"],
                "content": response,
            }]
            if friend_comment:
                player_turn.append({
                    "type": "text",
                    "text": ("(Your friend, watching from the chair beside "
                             f'you, says: "{friend_comment}")'),
                })
            messages.append({"role": "user", "content": player_turn})
            ui.stat(f"tokens: {prompt_total:,} prompt · {cache_read:,} from "
                    f"cache · {cache_write:,} new · {usage.output_tokens:,} out")

            turns_f.write(json.dumps({
                "turn": commands_sent,
                "game": games_played,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "thinking": thinking,
                "agent_text": agent_text,
                "command": command,
                "response": response,
                "friend": friend_comment,
                "finished": adv.finished,
                "prompt_tokens": prompt_total,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "output_tokens": usage.output_tokens,
            }) + "\n")
            turns_f.flush()

            if adv.finished:
                try:
                    finished_score, _ = adv.score()
                except Exception:
                    finished_score = 0
                best_score = max(best_score, finished_score)
                if not restart_on or commands_sent >= args.turns:
                    outcome = f"game over after {games_played} game(s)"
                    break
                # Restart: a fresh game, same cave — every bit of context kept.
                games_played += 1
                ui.info(f"-- this game ended — restarting (game {games_played}); "
                        "everything learned is kept --")
                adv = Adventure(seed=args.seed)
                messages[-1]["content"].append({
                    "type": "text",
                    "text": ("(That playthrough has ended. The game now resets "
                             "to the very beginning — but you keep everything "
                             "you have learned. A fresh game begins. The screen "
                             f"shows:\n\n{adv.intro})"),
                })
                friend_restart_pending = True
                continue
            if last_prompt_tokens > CONTEXT_SOFT_LIMIT:
                outcome = (f"context soft limit reached "
                           f"({last_prompt_tokens:,} tokens)")
                break
    except KeyboardInterrupt:
        outcome = "interrupted by user"
        ui.note("\n!! interrupted by user\n")

    # ── post-game reflection — META, outside the honest game interface ──────
    ask_reflection(client, args, messages, ui, system_prompt)

    try:
        cur_score, max_score = adv.score()
    except Exception:
        cur_score, max_score = 0, 350
    best_score = max(best_score, cur_score)
    ui.summary([
        ("outcome", outcome),
        ("games", str(games_played)),
        ("best score", f"{best_score} / {max_score}"),
        ("commands", str(commands_sent)),
        ("context", f"{last_prompt_tokens:,} tokens"),
        ("cache", f"{total_cache_read:,} tokens read from cache — "
                  f"{'caching active' if total_cache_read else 'no hits'}"),
    ])
    turns_f.close()
    ui.note(f"\nTranscript : logs/transcript-{run_id}.log")
    ui.note(f"Turns JSONL: {turns_path}")
    ui.close()


def ask_reflection(client, args, messages: list[dict], ui: UI,
                   system: str) -> None:
    """One final call, with no game tool: the agent steps out and reflects on
    its playthrough. This is harness-level meta — clearly marked — and is not
    part of the honest game interface."""
    prompt = ("The game session is over. Step out of the game for a moment and "
              "reflect briefly: What kind of game was this? How far do you think "
              "you got? What did you find hard, and what would you do "
              "differently?")
    last = messages[-1]
    if last["role"] == "user":
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"].append({"type": "text", "text": prompt})
    else:
        messages.append({"role": "user", "content": prompt})

    ui.rule("META — post-game reflection (outside the honest interface)")
    try:
        resp = call_model(client, args.model, messages, args.effort, system, ui)
    except Exception as exc:
        ui.note(f"\n(reflection call failed: {exc!r})\n")
        return
    blocks = [serialize_block(b) for b in resp.content]
    thinking = "\n".join(b["thinking"] for b in blocks if b["type"] == "thinking")
    text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
    if thinking:
        ui.record("thinking", thinking)
    if text:
        ui.record("reflection", text)


# ── selftest: exercise the plumbing with no API calls ───────────────────────
def selftest(args) -> None:
    print("SELFTEST — game wrapper + module imports (no API calls)\n")

    adv = Adventure(seed=args.seed)
    print(f"intro: {adv.intro.strip()!r}")
    assert adv.intro and not adv.finished, "game should start unfinished"

    script = ["no", "enter building", "get keys", "get lamp", "get bottle",
              "leave building", "xyzzy", "go east", "score", "no", "look"]
    for c in script:
        out = adv.command(c)
        assert isinstance(out, str) and out, f"empty response to {c!r}"
        head = out.strip().splitlines()[0][:62] if out.strip() else "(blank)"
        print(f"  > {c:16s} -> {head}")
    print(f"\n  finished: {adv.finished}   score: {adv.score()}")
    assert adv.command("") == "(The game shows its prompt again, awaiting a command.)"
    print("  empty-command handling: OK")

    adv2 = Adventure(seed=args.seed)
    assert not adv2.finished and adv2.intro, "restart should spawn a fresh game"
    print("  restart (fresh Adventure spawns cleanly): OK")

    import agents  # noqa: F401  — verify the modules import cleanly
    import tui     # noqa: F401
    print("  agents + tui modules import: OK")

    print("\nSELFTEST PASSED")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Run an LLM agent through Colossal Cave Adventure via an "
                    "honest one-tool interface.")
    p.add_argument("--turns", type=int, default=MAX_TURNS,
                   help=f"max commands the agent may send (default {MAX_TURNS})")
    p.add_argument("--model", default=MODEL,
                   help=f"player model id (default {MODEL})")
    p.add_argument("--effort", default=EFFORT,
                   choices=["low", "medium", "high", "xhigh", "max"],
                   help=f"thinking effort (default {EFFORT}; xhigh/max are Opus-only)")
    p.add_argument("--seed", type=int, default=GAME_SEED,
                   help=f"game RNG seed (default {GAME_SEED})")
    p.add_argument("--solo", action="store_true",
                   help="play alone — no kibitzer friend in the next chair")
    p.add_argument("--friend-model", default=FRIEND_MODEL,
                   help=f"model for the kibitzer friend (default {FRIEND_MODEL})")
    p.add_argument("--no-restart", action="store_true",
                   help="end the run at game-over instead of restarting "
                        "(restart keeps all context)")
    p.add_argument("--selftest", action="store_true",
                   help="exercise the plumbing without any API calls")
    args = p.parse_args()
    selftest(args) if args.selftest else run(args)


if __name__ == "__main__":
    main()

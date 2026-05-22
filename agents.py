"""Talking to Claude — the player and the kibitzer friend.

This is the API layer: model configuration, the prompts, and the two streaming
calls. It knows nothing about the game loop. It renders nothing itself — it
streams tokens through a `ui` object the caller passes in, so this module stays
a clean boundary around the Anthropic SDK.
"""
from __future__ import annotations

# ── models ──────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"        # the player. --model claude-opus-4-7 for top.
FRIEND_MODEL = "claude-haiku-4-5"  # the kibitzer in the next chair: small, fast.
EFFORT = "high"                    # adaptive-thinking effort: low|medium|high|
                                   # xhigh|max (xhigh/max are Opus-only).
MAX_TOKENS = 20000                 # per-turn output ceiling (thinking + command)


def effective_max_tokens(effort: str) -> int:
    """xhigh/max thinking needs more output headroom (per Opus 4.7 guidance)."""
    return 64000 if effort in ("xhigh", "max") else MAX_TOKENS


# ── prompts ─────────────────────────────────────────────────────────────────
# The player's system prompt is deliberately "blind": it explains the tool
# mechanics and nothing else — no game name, no world, no parser syntax. The
# game offers instructions on turn 1; the agent decides, exactly as a 1977
# player did. FRIEND_NOTE / RESTART_NOTE are appended only when those modes are
# on, so the player always knows the rules of its own situation.
SYSTEM_PROMPT = """\
You are playing a turn-based text adventure game.

Each turn, send a command with the `game` tool; the game replies with text. \
That text is the only information you have — there is no map, no inventory \
panel, and no hints beyond what the game itself prints.

Play as a human player would: explore, experiment, keep track of what you \
have seen, and try to progress as far as you can. The game is waiting for \
your first command."""

# The one and only tool. Its description is purely mechanical — no syntax hints,
# because syntax hints would be a parser crib the blind player does not get.
TOOL = {
    "name": "game",
    "description": (
        "Send a command to the text adventure game and receive the game's "
        "text response. This is the only way to interact with the game."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to send to the game.",
            }
        },
        "required": ["command"],
    },
}

# The kibitzer. Like the player's, this prompt is "blind": it never names the
# game. The friend reads the screen and the player's typed commands — not the
# player's private thinking — and it cannot touch the keyboard.
FRIEND_SYSTEM = """\
You are sitting in the chair next to a friend who is playing a text adventure \
game on the computer. You can see the screen and the commands your friend \
types — but you are NOT holding the keyboard. You cannot play. You can only \
watch and chime in, the way a friend does.

Each time the screen changes, react the way that friend would: a quick \
suggestion ("try going north"), a "wait — did you check the...?", a warning \
("careful, it said it's dark"), a hunch about a puzzle, a laugh, or just some \
encouragement. Keep it to one or two sentences — you are talking out loud, \
not writing. React to what just happened; stay in the moment."""

# Appended to the player's system prompt when the friend is present, so the
# player knows the asides come from a friend — honest: a 1977 player knew too.
FRIEND_NOTE = (
    "\n\nA friend is sitting in the chair next to you, watching the screen. "
    "Now and then they will say something — it reaches you as an aside marked "
    '"Your friend ... says". They are not the game, and they are not always '
    "right: treat it as a friend's two cents, to heed or wave off as you see "
    "fit. You hold the keyboard."
)

# Appended to the player's system prompt when restarts are enabled — a 1977
# player knew a dead end just meant loading the game up again.
RESTART_NOTE = (
    "\n\nIf the game ends — whether you win, lose, or die — it will start over "
    "from the very beginning, and you carry forward everything you have "
    "learned. Treat each ending as another attempt at the same cave."
)


# ── content-block plumbing ──────────────────────────────────────────────────
def serialize_block(b) -> dict:
    """Convert an SDK content block into a clean, re-sendable dict."""
    t = b.type
    if t == "text":
        return {"type": "text", "text": b.text}
    if t == "thinking":
        return {"type": "thinking", "thinking": b.thinking, "signature": b.signature}
    if t == "redacted_thinking":
        return {"type": "redacted_thinking", "data": b.data}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return b.model_dump()


# Note: the caller keeps `messages` strictly append-only — never edited or
# pruned. That keeps the request prefix byte-stable turn to turn, which is what
# makes Anthropic prompt caching hit (it is a prefix match). The cache_control
# below caches that prefix; adaptive-thinking blocks here are tiny, so there is
# nothing worth stripping anyway.


# ── the calls ───────────────────────────────────────────────────────────────
def call_model(client, model: str, messages: list[dict], effort: str,
               system: str, ui, tools: list | None = None):
    """One streaming API call for the player. Streams the agent's thinking and
    text through `ui`, token by token, as they arrive — then returns the final
    Message.

    Adaptive thinking is used: the only thinking mode on Opus 4.7, and supported
    on Sonnet 4.6. On Opus 4.7 thinking text is omitted unless `display` is set;
    Sonnet 4.6 returns summarized thinking by default and has no `display` field.
    """
    thinking = {"type": "adaptive"}
    if model.startswith("claude-opus-4-7"):
        thinking["display"] = "summarized"
    kwargs = dict(
        model=model,
        max_tokens=effective_max_tokens(effort),
        system=system,
        thinking=thinking,
        output_config={"effort": effort},
        messages=messages,
        cache_control={"type": "ephemeral"},  # cache the append-only prefix
    )
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}

    try:
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    kind = event.content_block.type
                    if kind == "thinking":
                        ui.stream_open("thinking", "grey50")
                    elif kind == "text":
                        ui.stream_open("the agent speaks", "white")
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "thinking_delta":
                        ui.stream_feed(delta.thinking)
                    elif delta.type == "text_delta":
                        ui.stream_feed(delta.text)
                elif event.type == "content_block_stop":
                    ui.stream_close()
            return stream.get_final_message()
    finally:
        ui.stream_close()


def call_friend(client, model: str, history: list[dict], ui) -> str:
    """The friend in the next chair: a quick reaction to what's on screen,
    streamed through `ui` in its own colour. No game tool and no thinking —
    just a glance and a comment."""
    parts: list[str] = []
    ui.stream_open("friend", "cyan")
    try:
        with client.messages.stream(model=model, max_tokens=300,
                                    system=FRIEND_SYSTEM, messages=history,
                                    cache_control={"type": "ephemeral"}) as stream:
            for event in stream:
                if (event.type == "content_block_delta"
                        and event.delta.type == "text_delta"):
                    ui.stream_feed(event.delta.text)
                    parts.append(event.delta.text)
    finally:
        ui.stream_close()
    return "".join(parts).strip()

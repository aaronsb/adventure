"""Terminal rendering for the Adventure harness.

A thin view layer over `rich`: it draws the run to the terminal — colour,
panels, rules, all width-aware — and at the same time mirrors a plain-text
transcript to a file. The player's thinking and the friend's asides arrive
here token by token and stream live; complete blocks (the game's reply, the
summary) render as panels.

`rich.Console` detects terminal width and whether stdout is a real terminal,
disabling colour automatically when piped — so a backgrounded run still
produces readable output and the transcript file is always clean plain text.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


class UI:
    """Draws the run to the terminal and mirrors a plain-text transcript file."""

    def __init__(self, transcript_path: str):
        self.console = Console()  # auto-detects width and terminal/colour
        self._f = open(transcript_path, "w", encoding="utf-8")
        self._streaming = False
        self._style = ""

    # ── transcript file mirror ──────────────────────────────────────────────
    def _file(self, text: str = "") -> None:
        self._f.write(text + "\n")
        self._f.flush()

    def _file_block(self, label: str, body: str) -> None:
        self._file(f"[{label}]")
        for line in (body or "").rstrip().splitlines():
            self._file(f"  {line}")
        self._file("")

    # ── structure ───────────────────────────────────────────────────────────
    def banner(self, title: str, lines: list[str]) -> None:
        self.console.print()
        self.console.print(Panel(Text("\n".join(lines)), title=title,
                                 title_align="left", border_style="cyan",
                                 expand=False))
        self._file("=" * 78)
        self._file(title)
        for ln in lines:
            self._file(f"  {ln}")
        self._file("=" * 78 + "\n")

    def rule(self, label: str) -> None:
        self.stream_close()
        self.console.print()
        self.console.rule(f"[bold]{label}[/bold]", style="bright_black")
        self._file("\n" + "=" * 78)
        self._file(f"  {label}")
        self._file("=" * 78)

    def info(self, text: str) -> None:
        """A harness-level aside — a nudge, a restart notice."""
        self.stream_close()
        self.console.print(f"  {text}", style="yellow")
        self._file(f"  {text}")

    def note(self, text: str) -> None:
        """A plain line to terminal and file — errors, file paths."""
        self.stream_close()
        self.console.print(text)
        self._file(text)

    # ── live streaming (terminal only — the file gets `record` afterwards) ──
    def stream_open(self, label: str, style: str) -> None:
        self.stream_close()
        self._style = style
        self._streaming = True
        self.console.print()
        self.console.print(f"  ── {label} ──", style=f"{style} italic")

    def stream_feed(self, text: str) -> None:
        self.console.print(Text(text, self._style), end="", soft_wrap=True)

    def stream_close(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    def record(self, label: str, body: str) -> None:
        """Mirror a streamed block into the transcript file (shown live already)."""
        self._file_block(label, body)

    # ── the game turn ───────────────────────────────────────────────────────
    def command(self, text: str) -> None:
        self.stream_close()
        self.console.print()
        self.console.print(f"  > {text}", style="bold green")
        self._file(f"\n  > {text}\n")

    def game(self, text: str) -> None:
        self.stream_close()
        self.console.print(Panel(Text((text or "").strip() or "(no output)"),
                                 title="game", title_align="left",
                                 border_style="green", expand=False))
        self._file_block("game", text)

    def stat(self, text: str) -> None:
        """A small dim metrics line — token counts, cache hits."""
        self.stream_close()
        self.console.print(f"  {text}", style="dim")
        self._file(f"  {text}")

    # ── close-out ───────────────────────────────────────────────────────────
    def summary(self, rows: list[tuple[str, str]]) -> None:
        self.stream_close()
        body = "\n".join(f"{k:<13}{v}" for k, v in rows)
        self.console.print()
        self.console.print(Panel(Text(body), title="summary", title_align="left",
                                 border_style="cyan", expand=False))
        self._file("\n" + "=" * 78)
        self._file("SUMMARY")
        for k, v in rows:
            self._file(f"  {k:<13}{v}")
        self._file("=" * 78)

    def close(self) -> None:
        self.stream_close()
        self._f.close()

"""What the user sees: streamed markdown, a spinner while the model thinks, compact tool
lines, approvals, and a footer with tokens and cost."""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from tarjuman import BlockStart, ReasoningDelta, TextDelta, ToolCall, ToolCallDelta, Usage

from .agent import (ContextChanged, ContextUse, Retrying, StateChanged, ToolFinished, ToolStarted, TurnEnded,
                    UIEvent)
from .tools import Spec

TOOL_LINES = 4


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def fmt_context(c: ContextUse) -> tuple[str, str]:
    """`ctx 23.4k / 1.0M · 2%` and a style that warms up as it fills. `~` = partly estimated
    (no reply from this model since the last change)."""
    used = ("" if c.exact else "~") + fmt_tokens(c.used)
    if c.usable is None:
        return f"ctx {used}", "dim"
    f = c.fraction or 0
    style = "red" if f >= 0.8 else "yellow" if f >= 0.5 else "dim"
    pct = "<1%" if 0 < f < 0.01 else f"{f:.0%}"
    return f"ctx {used} / {fmt_tokens(c.usable)} · {pct}", style


def fmt_cost(c: float) -> str:
    if c == 0:
        return "$0"
    if c < 0.01:
        return f"${c:.6f}".rstrip("0")   # $0.000069, not $0.0000
    return f"${c:.4f}"


def fmt_usage(u: Usage) -> str:
    total_in = u.input + u.cache_read + u.cache_write
    parts = [f"{fmt_tokens(total_in)} in" + (f" ({fmt_tokens(u.cache_read)} cached)" if u.cache_read else ""),
             f"{fmt_tokens(u.output)} out"]
    if u.cost is not None:
        parts.append(fmt_cost(u.cost))
    return " · ".join(parts)


def short(text: str) -> str:
    """Paths relative to the project folder."""
    cwd = str(Path.cwd())
    return text.replace(cwd + "/", "").replace(cwd, ".")


def summarize_call(call: ToolCall) -> str:
    try:
        a = call.args()
    except ValueError:
        return call.arguments[:80]
    if call.name == "bash":
        return short(a.get("command", ""))
    if call.name in ("grep", "glob"):
        where = [x for x in (a.get("glob"), a.get("path")) if x and x != "."]
        return f"{a.get('pattern', '')!r}" + (f" in {short(' '.join(where))}" if where else "")
    if call.name == "read" and a.get("offset"):
        return f"{short(a.get('path', ''))}:{a['offset']}"
    return short(str(a.get("path", "")))


class Terminal:
    def __init__(self, console: Console | None = None, *, auto_approve: bool = False,
                 interactive: bool = True, show_reasoning: bool = False):
        self.console = console or Console(highlight=False)
        self.auto = auto_approve
        self.interactive = interactive
        self.show_reasoning = show_reasoning
        self.always: set[str] = set()
        self.session = Usage()
        self.context: ContextUse | None = None  # set by the agent's ContextChanged events
        self._live: Live | None = None
        self._kind: str | None = None        # "text" | "reasoning" while streaming a block
        self._buf = ""
        self._started = 0.0
        self._shown: str | None = None       # tool call already printed by the approval prompt

    # --- live region ------------------------------------------------------------------------

    def _spinner(self, label: str) -> Spinner:
        secs = int(time.monotonic() - self._started)
        return Spinner("dots", Text(f" {label}" + (f" {secs}s" if secs >= 2 else ""), style="dim"))

    def _render(self):
        if self._kind == "text":
            return Markdown(self._buf)
        if self._kind == "reasoning" and self.show_reasoning:
            return Group(Text(self._buf[-2000:], style="dim italic"), self._spinner("thinking"))
        return self._spinner("thinking")

    def _update(self) -> None:
        if self._live is None:
            self._live = Live(self._render(), console=self.console, transient=True,
                              refresh_per_second=12)
            self._live.start()
        else:
            self._live.update(self._render())

    def _flush(self) -> None:
        """Stop the live region and print the finished block for good."""
        if self._live is not None:
            self._live.stop()
            self._live = None
        if self._kind == "text" and self._buf.strip():
            self.console.print(Markdown(self._buf))
        elif self._kind == "reasoning" and self.show_reasoning and self._buf.strip():
            self.console.print(Text(self._buf.strip(), style="dim italic"))
        self._kind, self._buf = None, ""

    # --- events -----------------------------------------------------------------------------

    def on(self, ev: UIEvent) -> None:
        if isinstance(ev, StateChanged):
            if ev.state == "thinking":
                self._flush()
                self._started = time.monotonic()
                self._update()
            return
        if isinstance(ev, BlockStart):
            self._flush()
            if ev.kind in ("text", "reasoning"):
                self._kind = ev.kind
                self._update()
            return
        if isinstance(ev, TextDelta | ReasoningDelta):
            self._buf += ev.text
            self._update()
            return
        if isinstance(ev, ToolCallDelta):
            return
        self._flush()
        c = self.console
        if isinstance(ev, ToolStarted):
            if ev.call.id != self._shown:
                self._tool_line(ev.call)
        elif isinstance(ev, ToolFinished):
            r = ev.result
            lines = r.text.splitlines() or [""]
            style = "red" if r.is_error else "dim"
            for i, line in enumerate(lines[:TOOL_LINES]):
                c.print(Text(("  ⎿ " if i == 0 else "    ") + short(line)[:200], style=style))
            if len(lines) > TOOL_LINES:
                c.print(Text(f"    … {len(lines) - TOOL_LINES} more lines", style="dim"))
        elif isinstance(ev, ContextChanged):
            self.context = ev.context
        elif isinstance(ev, Retrying):
            c.print(Text(f"  {ev.error.code}, retrying in {ev.wait:.0f}s (attempt {ev.attempt})",
                         style="yellow"))
        elif isinstance(ev, TurnEnded):
            self.session += ev.usage
            mark = {"done": ("✓", "green"), "interrupted": ("■ interrupted", "yellow")}.get(
                ev.reason, (f"■ stopped: {ev.reason}", "red"))
            footer = Text()
            footer.append(mark[0], style=mark[1])
            footer.append(f"  {ev.steps} step{'s' if ev.steps != 1 else ''} · {fmt_usage(ev.usage)}",
                          style="dim")
            if self.session.cost is not None:
                footer.append(f" · session {fmt_cost(self.session.cost)}", style="dim")
            if self.context is not None:
                text, style = fmt_context(self.context)
                footer.append(f" · {text}", style=style)
            c.print(footer)
            if ev.error:
                c.print(Text(f"  {ev.error}", style="red"))
            c.print()

    def _tool_line(self, call: ToolCall) -> None:
        line = Text()
        line.append("● ", style="cyan")
        line.append(call.name, style="bold")
        line.append(f"  {summarize_call(call)}")
        self.console.print(line)

    # --- approvals --------------------------------------------------------------------------

    def approve(self, call: ToolCall, spec: Spec) -> bool:
        self._flush()
        self._tool_line(call)
        self._shown = call.id
        if call.name in ("write", "edit"):
            self._preview(call)
        if self.auto or call.name in self.always:
            return True
        if not self.interactive:
            return False
        while True:
            try:
                answer = self.console.input(
                    f"  [dim]allow?[/dim] [bold]y[/bold]es / [bold]n[/bold]o / "
                    f"[bold]a[/bold]lways {call.name} › ").strip().lower()
            except EOFError:
                return False
            if answer in ("y", "yes", ""):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "always"):
                self.always.add(call.name)
                return True

    def _preview(self, call: ToolCall) -> None:
        try:
            a = call.args()
        except ValueError:
            return
        c = self.console
        if call.name == "edit":
            for line in str(a.get("old", "")).splitlines()[:8]:
                c.print(Text(f"    - {line}", style="red"))
            for line in str(a.get("new", "")).splitlines()[:8]:
                c.print(Text(f"    + {line}", style="green"))
        else:
            lines = str(a.get("content", "")).splitlines()
            for line in lines[:6]:
                c.print(Text(f"    + {line}", style="green"))
            if len(lines) > 6:
                c.print(Text(f"    … {len(lines) - 6} more lines", style="dim"))

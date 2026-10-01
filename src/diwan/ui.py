"""What the user sees: streamed markdown, a spinner while the model thinks, compact tool
lines, approvals, and a footer with tokens and cost."""

from __future__ import annotations

import time

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text
from tarjuman import BlockStart, ReasoningDelta, TextDelta, ToolCall, ToolCallDelta, Usage

from .context import ContextUse
from .events import (ContextChanged, ContextCleared, Retrying, StateChanged, ToolFinished,
                     ToolStarted, TurnEnded, UIEvent)
from .present import fmt_context, fmt_cost, fmt_usage, short, summarize_call
from .tools import Spec

TOOL_LINES = 4


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
        elif isinstance(ev, ContextCleared):
            c.print(Text(f"  ↺ {ev.text}; the log keeps them", style="dim"))
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

    def approve(self, call: ToolCall, spec: Spec, outside: bool = False) -> bool:
        """`outside`: the call reaches beyond the project. "Always" never covers that: it asks
        every time (unless -y)."""
        self._flush()
        self._tool_line(call)
        self._shown = call.id
        if outside:
            self.console.print(Text("    outside the project", style="yellow"))
        if call.name in ("write", "edit"):
            self._preview(call)
        if self.auto or (call.name in self.always and not outside):
            return True
        if not self.interactive:
            return False
        always = "" if outside else f" / [bold]a[/bold]lways {call.name}"
        while True:
            try:
                answer = self.console.input(
                    f"  [dim]allow?[/dim] [bold]y[/bold]es / [bold]n[/bold]o{always} › ").strip().lower()
            except EOFError:
                return False
            if answer in ("y", "yes", ""):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "always") and not outside:
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

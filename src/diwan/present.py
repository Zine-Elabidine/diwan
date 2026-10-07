"""How things read, the same in both front-ends: token counts, costs, the context gauge,
one-line summaries of tool calls, previews of file changes, how a turn ended."""

from __future__ import annotations

import json
from pathlib import Path

from tarjuman import ToolCall, Usage

from .context import ContextUse
from .events import ChildEvent, ContextSummarized, Reason, ToolFinished, ToolStarted, TurnEnded


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
    if call.name == "note":
        return f"{a.get('kind', '')}: {_first_line(str(a.get('text', '')))}"
    if call.name == "recall":
        return repr(a.get("query", ""))
    if call.name == "agent":
        return ("(read-only) " if a.get("readonly") else "") + _first_line(str(a.get("task", "")), 80)
    if call.name == "read" and a.get("offset"):
        return f"{short(a.get('path', ''))}:{a['offset']}"
    if "path" in a or call.name in ("read", "write", "edit"):
        return short(str(a.get("path", "")))
    # any other tool (an MCP server's): its arguments
    return short(" ".join(f"{k}={v if isinstance(v, str) else json.dumps(v)}" for k, v in a.items()))


def child_line(ev: ChildEvent) -> str | None:
    """One line for what a child agent did, or None for what isn't worth a line."""
    e = ev.event
    if isinstance(e, ToolStarted):
        return f"↳ {e.call.name} {summarize_call(e.call)}"
    if isinstance(e, ToolFinished) and e.result.is_error:
        return f"↳ {e.call.name} failed: {_first_line(e.result.text, 80)}"
    if isinstance(e, ContextSummarized):
        return f"↳ ↺ {e.text}"
    if isinstance(e, TurnEnded):
        return f"↳ agent finished ({e.reason}): {e.steps} steps · {fmt_usage(e.usage)}"
    return None


def fmt_window(n: int) -> str:
    """A catalog window, rounded: `1M`, `200k`."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{n // 1000}k"


def preview(call: ToolCall, limit: int) -> list[tuple[str, str]]:
    """What a write or edit changes, as (line, style): removed lines "- ", added "+ ", at most
    `limit` of each, then how many more. Empty for other calls or unreadable arguments."""
    try:
        a = call.args()
    except ValueError:
        return []
    if call.name == "edit":
        parts = [("- ", "red", a.get("old", "")), ("+ ", "green", a.get("new", ""))]
    elif call.name == "write":
        parts = [("+ ", "green", a.get("content", ""))]
    else:
        return []
    out: list[tuple[str, str]] = []
    for sign, style, text in parts:
        lines = str(text).splitlines()
        out += [(sign + line, style) for line in lines[:limit]]
        if len(lines) > limit:
            out.append((f"… {len(lines) - limit} more lines", "dim"))
    return out


def turn_mark(reason: Reason) -> tuple[str, str]:
    """The mark at the start of a turn's footer, and its style."""
    return {"done": ("✓", "green"), "interrupted": ("■ interrupted", "yellow")}.get(
        reason, (f"■ stopped: {reason}", "red"))


def _first_line(text: str, width: int = 100) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= width else line[:width - 1] + "…"


def fmt_notes(notes: list[tuple[str, str]]) -> str:
    """/notes: one line per note, `kind: text`."""
    return "\n".join(f"{kind}: {text}" for kind, text in notes) or "no notes yet"

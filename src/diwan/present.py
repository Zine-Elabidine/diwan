"""How things read, the same in both front-ends: token counts, costs, the context gauge,
one-line summaries of tool calls."""

from __future__ import annotations

from pathlib import Path

from tarjuman import ToolCall, Usage

from .context import ContextUse


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


def fmt_window(n: int) -> str:
    """A catalog window, rounded: `1M`, `200k`."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{n // 1000}k"

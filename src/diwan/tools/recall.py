"""Searching what the model no longer sees: the last tier of docs/research-context.md §5.
Clearing and summaries only change what is sent; the log keeps every message, every tool
output in full. `recall` searches it, so a detail dropped from the context can still be found
exactly instead of guessed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tarjuman import Message, ToolCall

from .base import Tool, ToolContext, ToolError, schema

if TYPE_CHECKING:
    from ..log import Log

MAX_HITS = 20
MAX_CHARS = 8_000
LINE = 300   # a matching line longer than this is cut


class Recall(Tool):
    name = "recall"
    description = ("Search this session's full history for exact text: earlier tool outputs "
                   "(including ones cleared to save context), files you read, commands and "
                   "errors, and messages replaced by a summary. Use it when you need an exact "
                   "detail from earlier instead of answering from memory. Plain text, not case "
                   "sensitive, not a regex. Returns the newest matching lines.")
    parameters = schema({
        "query": {"type": "string", "description": "text to find: a name, a value, an error"}},
        ["query"])
    readonly = True   # reads only the session log

    def run(self, ctx: ToolContext, query: str) -> str:
        if ctx.log is None:
            raise ToolError("no session log to search")
        q = query.strip().lower()
        if not q:
            raise ToolError("the query is empty")
        hits = search(ctx.log, q)
        if not hits:
            return f"No match for {query!r} in this session."
        out, total = [], 0
        for h in reversed(hits[-MAX_HITS:]):   # newest first while filling, then back in order
            if total + len(h) > MAX_CHARS:
                break
            out.append(h)
            total += len(h)
        out.reverse()
        more = len(hits) - len(out)
        tail = f"\n({more} older matches not shown: use a more specific query)" if more else ""
        return "\n".join(out) + tail


def search(log: Log, q: str) -> list[str]:
    """Every line on the current branch containing `q` (lowercase), oldest first, labelled
    with where it came from. recall's own calls and results are skipped: they only repeat
    what was found before."""
    messages = log.messages()
    calls = {c.id: c for m in messages for c in m.tool_calls}
    hits = []
    for k, m in enumerate(messages):
        for where, text in _texts(m, calls):
            for n, line in enumerate(text.splitlines(), 1):
                if q in line.lower():
                    hits.append(f"[message {k}, {where}, line {n}] {_short(line.strip())}")
    return hits


def _texts(m: Message, calls: dict[str, ToolCall]) -> list[tuple[str, str]]:
    if m.role == "user":
        return [("user", m.text)]
    if m.role == "assistant":
        out = [("your reply", m.text)] if m.text else []
        out += [(f"your {c.name} call", c.arguments) for c in m.tool_calls if c.name != "recall"]
        return out
    if m.role == "tool":
        out = []
        for r in m.tool_results:
            call = calls.get(r.call_id)
            if call is None or call.name == "recall":
                continue
            out.append((f"{call.name} {_target(call)} output".replace("  ", " "), r.text))
        return out
    return []


def _target(call: ToolCall) -> str:
    try:
        a = call.args()
    except ValueError:
        return ""
    for key in ("path", "pattern", "command"):
        if a.get(key):
            v = str(a[key])
            return v if len(v) <= 60 else v[:57] + "..."
    return ""


def _short(line: str) -> str:
    return line if len(line) <= LINE else line[:LINE] + " [...]"

"""Searching what the model no longer sees: the last tier of docs/research-context.md §5.
Clearing and summaries only change what is sent; the log keeps every message, every tool
output in full. `recall` searches it, so a detail dropped from the context can still be found
exactly instead of guessed."""

from __future__ import annotations

import re
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
                   "detail from earlier instead of answering from memory. Give a few words, not "
                   "case sensitive, not a regex: a line matches when the line, or the call it came "
                   "from, contains every word (`media timeout` finds `timeout_ms = 1050` in the "
                   "output of `diag.py media`). If no line has every word, the closest matches are "
                   "returned. Returns the newest matching lines.")
    parameters = schema({
        "query": {"type": "string",
                  "description": "words to find: a name, a value, an error, where it came from"}},
        ["query"])
    readonly = True   # reads only the session log

    def run(self, ctx: ToolContext, query: str) -> str:
        if ctx.log is None:
            raise ToolError("no session log to search")
        words = _words(query)
        if not words:
            raise ToolError("the query is empty")
        hits, matched = search(ctx.log, words)
        if not hits:
            return f"No match for {query!r} in this session."
        # tool outputs first (they hold the exact details), newest first; then the conversation
        ranked = sorted(range(len(hits)), key=lambda i: (not hits[i][0], -i))
        keep, total = [], 0
        for i in ranked[:MAX_HITS]:
            if total + len(hits[i][1]) > MAX_CHARS:
                break
            keep.append(i)
            total += len(hits[i][1])
        out = [hits[i][1] for i in sorted(keep)]   # shown in session order
        more = len(hits) - len(out)
        tail = f"\n({more} older matches not shown: use a more specific query)" if more else ""
        head = "" if matched == len(words) else \
            f"No line has every word of {query!r}; these have {matched} of its {len(words)}.\n"
        return head + "\n".join(out) + tail


def search(log: Log, words: list[str]) -> tuple[list[tuple[bool, str]], int]:
    """The lines on the current branch that match the most of `words` (lowercase), oldest
    first, as (from a tool output, labelled line), and how many words they match. A word
    matches where a word starts (`port` finds `port = 80`, not `report`), in the line or in its
    label (the call that produced it); every hit has at least one in the line itself, or
    `media` alone would return all of `diag.py media`'s output.
    recall's own calls and results are skipped: they only repeat what was found before."""
    messages = log.messages()
    calls = {c.id: c for m in messages for c in m.tool_calls}
    pats = [re.compile(r"(?<![a-z0-9])" + re.escape(w)) for w in words]
    scored: list[tuple[int, bool, str]] = []
    for k, m in enumerate(messages):
        for where, text in _texts(m, calls):
            label = where.lower()
            for n, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if not any(p.search(low) for p in pats):
                    continue
                score = sum(bool(p.search(low) or p.search(label)) for p in pats)
                hit = f"[message {k}, {where}, line {n}] {_short(line.strip())}"
                scored.append((score, m.role == "tool", hit))
    best = max((s for s, _, _ in scored), default=0)
    # the best tool-output lines always come too: a question that repeats every word of the
    # query would otherwise outrank the output that answers it
    best_tool = max((s for s, tool, _ in scored if tool), default=0)
    return [(tool, h) for s, tool, h in scored if s == best or (tool and s == best_tool)], best


def _words(query: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[^\s`'\",;()]+", query.lower())))


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

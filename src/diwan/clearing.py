"""Keeping the context small: the middle tier of docs/research-context.md §5.

Old tool outputs, and the file contents inside old write/edit calls, are replaced by a short
line saying what they were. Messages and the calls themselves stay. Nothing is deleted: a
"mask" event in the log records what was cleared and the exact text that replaces it, so every
later request is built the same way (stable prefix for the cache, exact replay)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tarjuman import Block, Message, ToolCall, ToolResult

MASK_AT = 0.5             # start clearing when the context is half full
KEEP_RECENT = 0.25        # newest tool outputs kept verbatim: this share of the usable window...
KEEP_RECENT_MAX = 40_000  # ...but no more than this many tokens
MIN_SAVING = 0.10         # clear in batches of at least this share of the window (each batch
                          # changes the request's prefix and costs a cache miss)
BIG_ARG = 1_000           # write/edit argument values longer than this (characters) are cleared
FILE_ARGS = ("content", "old", "new")


@dataclass
class Plan:
    entries: dict[str, str]   # "out:<call id>" / "args:<call id>" -> replacement text
    cleared: int              # tool outputs cleared
    saved: int                # tokens saved, estimated


def apply(messages: list[Message], masked: dict[str, str]) -> list[Message]:
    """The messages as the model sees them: masked outputs and arguments replaced."""
    if not masked:
        return messages
    return [_masked(m, masked) for m in messages]


def _masked(m: Message, masked: dict[str, str]) -> Message:
    if m.role == "tool" and any(f"out:{r.call_id}" in masked for r in m.tool_results):
        return _copy(m, [ToolResult(b.call_id, masked[f"out:{b.call_id}"], b.is_error, b.name)
                         if isinstance(b, ToolResult) and f"out:{b.call_id}" in masked else b
                         for b in m.content])
    if m.role == "assistant" and any(f"args:{c.id}" in masked for c in m.tool_calls):
        return _copy(m, [ToolCall(b.id, b.name, masked[f"args:{b.id}"])
                         if isinstance(b, ToolCall) and f"args:{b.id}" in masked else b
                         for b in m.content])
    return m


def plan(messages: list[Message], masked: dict[str, str], usable: int,
         chars_per_token: float) -> Plan | None:
    """What to clear now, or None: the newest outputs (up to KEEP_RECENT of the window) and the
    latest step stay; the rest is cleared if that saves at least MIN_SAVING of the window."""
    calls = {c.id: c for m in messages if m.role == "assistant" for c in m.tool_calls}
    last_step = _last_step_ids(messages)
    keep = min(int(usable * KEEP_RECENT), KEEP_RECENT_MAX)

    def tok(text: str) -> int:
        return round(len(text) / chars_per_token)

    entries: dict[str, str] = {}
    saved = cleared = kept = 0
    recent = True   # the newest outputs, contiguously, up to `keep` tokens; then everything older
    for m in reversed(messages):
        if m.role != "tool":
            continue
        for r in reversed(m.tool_results):
            size = tok(r.text)
            if recent and (r.call_id in last_step or kept + size <= keep):
                kept += size
                continue
            recent = False
            call = calls.get(r.call_id)
            text = _placeholder(call, r)
            if f"out:{r.call_id}" not in masked and tok(text) < size:
                entries[f"out:{r.call_id}"] = text
                saved += size - tok(text)
                cleared += 1
            if call is None or f"args:{call.id}" in masked:
                continue
            slim = _slim_args(call)
            if slim is not None:
                entries[f"args:{call.id}"] = slim
                saved += tok(call.arguments) - tok(slim)
    if not entries or saved < usable * MIN_SAVING:
        return None
    return Plan(entries, cleared, saved)


def _placeholder(call: ToolCall | None, r: ToolResult) -> str:
    what = f"{call.name} {_describe(call)}".strip() if call else "a tool call"
    lines = r.text.count("\n") + 1 if r.text else 0
    return (f"[Output cleared to save context: {what}, {lines} line{'s' if lines != 1 else ''}, "
            f"{len(r.text):,} characters. Run it again if you need it.]")


def _describe(call: ToolCall) -> str:
    try:
        a = call.args()
    except ValueError:
        return ""
    for key in ("path", "pattern", "command"):
        if a.get(key):
            v = str(a[key])
            return v if len(v) <= 80 else v[:77] + "..."
    return ""


def _slim_args(call: ToolCall) -> str | None:
    """The call's arguments with big file contents replaced by their size, still valid JSON."""
    try:
        a = call.args()
    except ValueError:
        return None
    slim: dict[str, Any] = dict(a)
    for k in FILE_ARGS:
        if isinstance(a.get(k), str) and len(a[k]) > BIG_ARG:
            slim[k] = f"[{len(a[k]):,} characters cleared to save context]"
    return json.dumps(slim, ensure_ascii=False) if slim != a else None


def _last_step_ids(messages: list[Message]) -> set[str]:
    for m in reversed(messages):
        if m.role == "assistant" and m.tool_calls:
            return {c.id for c in m.tool_calls}
    return set()


def _copy(m: Message, content: list[Block]) -> Message:
    return Message(m.role, content, m.provider, m.model, m.usage, m.stop, m.protocol,
                   m.response_model, m.replay, m.error, m.partial)


def saved_text(p: Plan) -> str:
    return f"cleared {p.cleared} old tool output{'s' if p.cleared != 1 else ''} (~{p.saved:,} tokens)"


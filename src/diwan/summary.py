"""The summary tier: the "Old" row of docs/research-context.md §5. Pure functions: where a
summary cuts, what the summarizer is sent, and the exact text stored in the "summary" event.
Asking the model and deciding when are elsewhere (context.py, agent.py).

The stored text has two parts. The model's part is a handoff note, updated at each summary
rather than written again. The mechanical part is copied from the log every time: the user's
own messages, the notes, the files changed. It is never sent back to the summarizer, so it
can't be paraphrased away or copied twice."""

from __future__ import annotations

from tarjuman import Message, Request, tokens

from . import clearing
from .tools.notes import KINDS

TRIGGER = 0.75          # summarize when the context is this full...
HARD_LIMIT = 200_000    # ...or holds this many tokens, whichever comes first
TARGET = 0.40           # and cut so the request lands about here afterwards

# shares of the effective window
REPLY = 0.05            # the summarizer's answer (max_tokens)...
REPLY_MAX = 8_000       # ...but no more than this
USER_TEXT = 0.05        # the user's own messages, newest kept
NOTES_TEXT = 0.03       # the notes, newest kept
ITEM = 0.01             # one message or tool output in the transcript...
ITEM_MIN, ITEM_MAX = 300, 4_000   # ...in characters, within these bounds

HEADER = ("This conversation was summarized to save context. The full session log is kept: "
          "nothing was deleted.")

PROMPT = """You are writing a handoff note for an AI coding agent that will continue this \
session with no other memory of it. Below is {what}. Write the note in these sections:

## Goal
## Constraints and preferences
## Done
## In progress
## Decisions (with the reason)
## Rejected approaches (and why)
## Next steps
## Critical context

Keep exact file paths, function names, commands and error messages. Leave out anything the \
agent can find again by reading the files. No preamble: start with "## Goal".
{previous}
<transcript>
{transcript}
</transcript>"""


def effective(usable: int) -> int:
    """The window the shares are taken of: the usable window, or less on a very large one, so
    the 75% trigger falls at HARD_LIMIT at most."""
    return min(usable, int(HARD_LIMIT / TRIGGER))


def due(used: int, usable: int) -> bool:
    return used >= TRIGGER * effective(usable)


def reply_tokens(usable: int) -> int:
    return min(int(effective(usable) * REPLY), REPLY_MAX)


def tail_budget(usable: int) -> int:
    """Tokens left for the messages kept verbatim: the target, minus the summary itself (the
    model's part and the mechanical part, at their caps)."""
    e = effective(usable)
    return max(int(e * TARGET) - reply_tokens(usable) - int(e * (USER_TEXT + NOTES_TEXT)), 0)


def valid_cut(messages: list[Message], cut: int) -> bool:
    """A cut replaces messages[:cut]. It may not separate a tool result from its call: the
    result's message would come first, answering a call the request no longer has."""
    if not 0 < cut <= len(messages):
        return False
    return cut == len(messages) or messages[cut].role != "tool"


def choose_cut(messages: list[Message], masked: dict[str, str], after: int, budget: int,
               chars_per_token: float) -> int | None:
    """Where to cut: the earliest valid cut past `after` (the previous summary's cut) whose
    tail, as sent, fits `budget` tokens; before an assistant message when there is one.
    The model's latest message and what follows it (its tool results, the user's new message)
    are always kept. None when the cut can't move forward."""
    sent = clearing.apply(messages, masked)
    limit = max((i for i, m in enumerate(messages) if m.role == "assistant"),
                default=len(messages) - 1)
    options = [c for c in range(after + 1, limit + 1) if valid_cut(messages, c)]
    if not options:
        return None
    size = [0] * (len(sent) + 1)            # size[c]: tokens of sent[c:]
    for i in range(len(sent) - 1, -1, -1):
        size[i] = size[i + 1] + tokens.estimate([sent[i]], None, chars_per_token)
    fits = [c for c in options if size[c] <= budget]
    if not fits:
        return options[-1]                  # keep as little as allowed
    clean = [c for c in fits if messages[c].role == "assistant"]
    return clean[0] if clean else fits[0]


def transcript(messages: list[Message], masked: dict[str, str], item_chars: int) -> str:
    """The messages as the model saw them (masks applied), as plain text for the summarizer:
    no reasoning, long items cut in the middle. Plain text, so the summarizer's request has no
    calls and results to pair up."""
    messages = clearing.apply(messages, masked)
    calls = {c.id: c for m in messages for c in m.tool_calls}
    lines = []
    for m in messages:
        if m.role in ("user", "system") and m.text:
            lines.append(f"[{m.role}] {_cap(m.text, item_chars)}")
        elif m.role == "assistant":
            if m.text:
                lines.append(f"[assistant] {_cap(m.text, item_chars)}")
            for c in m.tool_calls:
                lines.append(f"[call {c.name}] {_cap(c.arguments, item_chars)}")
        elif m.role == "tool":
            for r in m.tool_results:
                call = calls.get(r.call_id)
                name = call.name if call else "tool"
                error = " (error)" if r.is_error else ""
                lines.append(f"[{name} result{error}] {_cap(r.text, item_chars)}")
    return "\n".join(lines)


def item_chars(usable: int, chars_per_token: float) -> int:
    return max(ITEM_MIN, min(int(effective(usable) * ITEM * chars_per_token), ITEM_MAX))


def request(model: str, previous: str | None, text: str, max_tokens: int) -> Request:
    """The summarizer's request: one user message, no tools, no reasoning."""
    if previous:
        what = "the previous handoff note and the part of the session that came after it"
        prev = ("\nUpdate this note with the transcript; keep what still holds.\n"
                f"<note>\n{previous}\n</note>\n")
    else:
        what = "the transcript of the session so far"
        prev = ""
    body = PROMPT.format(what=what, previous=prev, transcript=text)
    return Request(model, [Message.user(body)], None, max_tokens=max_tokens, reasoning="off")


def user_messages(messages: list[Message], chars: int) -> list[str]:
    """The user's own messages, newest kept within `chars`, oldest first."""
    out, total = [], 0
    for m in reversed(messages):
        if m.role != "user" or not m.text.strip():
            continue
        text = _cap(m.text.strip(), max(chars // 4, ITEM_MIN))
        if total + len(text) > chars:
            break
        out.append(text)
        total += len(text)
    return out[::-1]


def notes(messages: list[Message]) -> list[tuple[str, str]]:
    """The model's notes, oldest first, as (kind, text)."""
    out = []
    for m in messages:
        for c in m.tool_calls:   # finished calls only: interrupted ones sit in `partial`
            if c.name != "note":
                continue
            try:
                a = c.args()
            except ValueError:
                continue
            if a.get("kind") in KINDS and str(a.get("text") or "").strip():
                out.append((a["kind"], str(a["text"]).strip()))
    return out


def files_changed(messages: list[Message]) -> list[str]:
    """Paths written or edited successfully, in the order first changed."""
    failed = {r.call_id for m in messages for r in m.tool_results if r.is_error}
    out: list[str] = []
    for m in messages:
        for c in m.tool_calls:
            if c.name not in ("write", "edit") or c.id in failed:
                continue
            try:
                path = str(c.args().get("path") or "")
            except ValueError:
                continue
            if path and path not in out:
                out.append(path)
    return out


def final_text(model_text: str, replaced: list[Message], usable: int,
               chars_per_token: float) -> str:
    """The exact text stored in the event and sent: the header, the model's note, then the
    user's messages, the notes and the files changed, taken from the replaced messages."""
    e = effective(usable)
    parts = [HEADER, model_text.strip()]
    said = user_messages(replaced, int(e * USER_TEXT * chars_per_token))
    if said:
        parts.append("## The user's messages (verbatim, oldest first)\n"
                     + "\n".join(f"- {t}" for t in said))
    kept = _newest(notes(replaced), int(e * NOTES_TEXT * chars_per_token))
    if kept:
        parts.append("## Notes (verbatim)\n" + "\n".join(f"- {k}: {t}" for k, t in kept))
    files = files_changed(replaced)
    if files:
        parts.append("## Files changed\n" + "\n".join(f"- {p}" for p in files))
    return "\n\n".join(parts)


def _newest(items: list[tuple[str, str]], chars: int) -> list[tuple[str, str]]:
    out, total = [], 0
    for k, t in reversed(items):
        if total + len(t) > chars:
            break
        out.append((k, t))
        total += len(t)
    return out[::-1]


def _cap(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    half = n // 2
    return f"{text[:half]} [... {len(text) - 2 * half:,} characters cut ...] {text[-half:]}"

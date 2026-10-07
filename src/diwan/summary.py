"""The summary tier: the "Old" row of docs/research-context.md §5. Pure functions: where a
summary cuts, what the summarizer is sent, and the exact text stored in the "summary" event.
Asking the model and deciding when are elsewhere (context.py, agent.py).

The stored text has two parts. The model's part is a handoff note, updated at each summary
rather than written again. The mechanical part is copied from the log every time: the user's
own messages, the notes, the files changed. It is never sent back to the summarizer, so it
can't be paraphrased away or copied twice."""

from __future__ import annotations

from tarjuman import Message, Request, Tool, tokens

from . import clearing
from .tools.notes import KINDS

TRIGGER = 0.75          # summarize when the context is this full...
HARD_LIMIT = 200_000    # ...or holds this many tokens, whichever comes first
TARGET = 0.40           # and cut so the request lands about here afterwards

# shares of the effective window
REPLY = 0.05            # the summarizer's answer (max_tokens)...
REPLY_MIN, REPLY_MAX = 2_000, 8_000   # ...within these bounds (a full note is ~500-1,500)
USER_TEXT = 0.05        # the user's own messages, newest kept
NOTES_TEXT = 0.03       # the notes, newest kept
# one message or tool output in the transcript, in characters: a fair share of the room, within
# these bounds (the upper one keeps the summarizer's request cheap on large windows)
ITEM_MIN, ITEM_MAX = 300, 4_000

HEADER = ("This conversation was summarized to save context. The full session log is kept: "
          "nothing was deleted. The summary leaves out details: when you need an exact value, "
          "line or error from earlier, read the file or run the command again instead of "
          "answering from memory.")

SECTIONS = """## Goal
## Constraints and preferences
## Done
## In progress
## Decisions (with the reason)
## Rejected approaches (and why)
## Next steps
## Critical context

Keep exact file paths, function names, commands and error messages. Leave out anything the \
agent can find again by reading the files. No preamble: start with "## Goal"."""

PROMPT = """You are writing a handoff note for an AI coding agent that will continue this \
session with no other memory of it. Below is {what}. Write the note in these sections:

""" + SECTIONS + """
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
    share = min(max(int(effective(usable) * REPLY), REPLY_MIN), REPLY_MAX)
    return min(share, usable // 4)   # on a tiny window the note still leaves room to send


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


def transcript(messages: list[Message], masked: dict[str, str], room: int) -> str:
    """The messages as the model saw them (masks applied), as plain text for the summarizer:
    no reasoning, long items cut in the middle so the whole fits in about `room` characters.
    Plain text, so the summarizer's request has no calls and results to pair up."""
    messages = clearing.apply(messages, masked)
    calls = {c.id: c for m in messages for c in m.tool_calls}
    items: list[tuple[str, str]] = []
    for m in messages:
        if m.role in ("user", "system") and m.text:
            items.append((f"[{m.role}]", m.text))
        elif m.role == "assistant":
            if m.text:
                items.append(("[assistant]", m.text))
            items += [(f"[call {c.name}]", c.arguments) for c in m.tool_calls]
        elif m.role == "tool":
            for r in m.tool_results:
                call = calls.get(r.call_id)
                name = call.name if call else "tool"
                error = " (error)" if r.is_error else ""
                items.append((f"[{name} result{error}]", r.text))
    labels = sum(len(label) + 2 for label, _ in items)
    cap = max(ITEM_MIN, min(fair_cap([len(t) for _, t in items], room - labels), ITEM_MAX))
    return "\n".join(f"{label} {cut_middle(t, cap)}" for label, t in items)


def fair_cap(sizes: list[int], room: int) -> int:
    """The largest per-item length that fits `room` in total: items shorter than it stay whole,
    the long ones share what is left equally."""
    left, n = room, len(sizes)
    for i, size in enumerate(sorted(sizes)):
        share = left // (n - i)
        if size > share:
            return max(share, 0)
        left -= size
    return max(sizes, default=0)


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


# the other way, as Codex and Claude Code do it: the conversation is sent again exactly as the
# model saw it (same system prompt, tools and messages, so the provider's prompt cache is reused
# and nothing is shortened), with this request added at the end
IN_PLACE = """Stop working on the task for a moment and do not call any tool. Write a handoff \
note for an AI coding agent that will continue this session with no other memory of it, \
covering the conversation above{previous}. The most recent part, from {keep}, stays in the \
context word for word after your note: put the detail into what comes before it, and keep \
Goal, In progress and Next steps current. Write it in these sections:

""" + SECTIONS


def note_text(text: str) -> str:
    """The note from the summarizer's reply: from "## Goal" on. A model that thinks out loud
    first (seen with DeepSeek when the request comes at the end of the real conversation) puts
    that before it. Empty when there is no note."""
    start = text.find("## Goal")
    return text[start:].strip() if start >= 0 else ""


def in_place_request(model: str, system: str, tools: list[Tool], view: list[Message],
                     kept: Message, updates: bool, max_tokens: int) -> Request:
    """The summarizer's request in place: the whole view, exactly as last sent (the provider
    caches the newest request: a shorter prefix may have expired, at Anthropic in 5 minutes),
    then the ask. `kept` is the first message that stays after the note."""
    previous = (" (its first message is the previous note: update it and keep what still holds)"
                if updates else "")
    ask = Message.user(IN_PLACE.format(previous=previous, keep=_quote(kept)))
    return Request(model, [Message.system(system), *view, ask], tools, max_tokens=max_tokens)


def _quote(m: Message) -> str:
    """How the ask points at a message: its opening words, or its first tool call."""
    who = {"user": "the user's message", "assistant": "your message"}.get(m.role, "the message")
    if m.text.strip():
        words = " ".join(m.text.split())
        return f'{who} that begins "{words[:80]}{"..." if len(words) > 80 else ""}"'
    if m.tool_calls:
        return f"your {m.tool_calls[0].name} call ({_first_args(m.tool_calls[0].arguments)})"
    return who


def _first_args(arguments: str) -> str:
    one = " ".join(arguments.split())
    return one if len(one) <= 80 else one[:80] + "..."


def user_messages(messages: list[Message], chars: int) -> list[str]:
    """The user's own messages, newest kept within `chars`, oldest first."""
    out, total = [], 0
    for m in reversed(messages):
        if m.role != "user" or not m.text.strip():
            continue
        text = cut_middle(m.text.strip(), max(chars // 4, ITEM_MIN))
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


def cut_middle(text: str, n: int) -> str:
    """`text`, or its start and end with the middle cut out, in about `n` characters."""
    if len(text) <= n:
        return text
    half = n // 2
    return f"{text[:half]} [... {len(text) - 2 * half:,} characters cut ...] {text[-half:]}"

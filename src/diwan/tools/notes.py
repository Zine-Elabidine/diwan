"""Notes the model keeps for itself: decisions, rejected approaches, progress. They are tool
calls in the session log, so they follow branches and are never lost; when older parts of the
conversation stop being sent, the notes still are (context.py)."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolError, schema

KINDS = ("decision", "rejected", "progress")


class Note(Tool):
    name = "note"
    description = ("Keep a short note for later in this session: a `decision` (what you chose "
                   "and why), a `rejected` approach (what you tried or ruled out and why), or "
                   "`progress` (what is done, what is next). Notes are kept when older parts of "
                   "the conversation are dropped, so write what you would need to continue "
                   "without them. Call it alongside your other tool calls, not on its own.")
    parameters = schema({
        "kind": {"type": "string", "enum": list(KINDS)},
        "text": {"type": "string", "description": "one or two sentences, with the reason"}},
        ["kind", "text"])
    readonly = True   # writes only to the session log: no approval needed

    def run(self, ctx: ToolContext, kind: str, text: str) -> str:
        if kind not in KINDS:
            raise ToolError(f"kind must be one of: {', '.join(KINDS)}")
        if not text.strip():
            raise ToolError("the note is empty")
        return "Noted."

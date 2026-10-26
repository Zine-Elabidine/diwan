"""What a `write` or `edit` changes, as a diff the UI can draw: numbered rows of context,
removed and added lines, with the words that changed inside a replaced line marked.

`change()` works out the file before and after the call (it reads the file; nothing is
written); `rows()` turns two texts into hunks of rows. Both are pure apart from the read, so
the UI can show the same diff in the approval dialog, in the chat, and when a session is
replayed."""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from tarjuman import ToolCall

CONTEXT = 3        # unchanged lines kept around each change
MAX_ROWS = 80      # longer diffs end with "… N more lines"


@dataclass
class Row:
    kind: str                     # "ctx", "add", "del", or "gap" (lines skipped between hunks)
    old: int | None = None        # 1-based line number in the file before
    new: int | None = None        # 1-based line number in the file after
    spans: list[tuple[str, bool]] = field(default_factory=list)   # (text, changed)

    @property
    def text(self) -> str:
        return "".join(s for s, _ in self.spans)


@dataclass
class Change:
    path: str
    before: str
    after: str
    created: bool = False         # the file did not exist before (a new `write`)


def change(call: ToolCall, read: Callable[[str], str | None], applied: bool = False) -> Change | None:
    """The file before and after `call`, or None for other tools or unreadable arguments.
    `read(path)` returns the file's text, or None if it doesn't exist. `applied`: the call
    already ran (a replayed session), so the file holds the result; an edit is undone in
    memory to get the before."""
    if call.name not in ("edit", "write"):
        return None
    try:
        a = call.args()
    except ValueError:
        return None
    path = str(a.get("path", ""))
    current = read(path) if path else None
    if call.name == "write":
        content = str(a.get("content", ""))
        if applied:   # what was there before an applied write is gone: show it as written
            return Change(path, "", content, created=True)
        return Change(path, current or "", content, created=current is None)
    old, new = str(a.get("old", "")), str(a.get("new", ""))
    every = bool(a.get("replace_all"))
    if current is not None and applied and new and new in current:
        before = current.replace(new, old) if every else current.replace(new, old, 1)
        return Change(path, before, current)
    if current is not None and not applied and old and old in current:
        after = current.replace(old, new) if every else current.replace(old, new, 1)
        return Change(path, current, after)
    return Change(path, old, new)   # the file can't tell us where: show the snippet itself


def rows(before: str, after: str, context: int = CONTEXT,
         limit: int = MAX_ROWS) -> tuple[list[Row], int, int]:
    """The diff of two texts as rows, plus how many lines were added and removed."""
    a, b = before.splitlines(), after.splitlines()
    out: list[Row] = []
    adds = dels = 0
    groups = list(difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(context))
    for gi, group in enumerate(groups):
        if gi:
            out.append(Row("gap"))
        for op, i1, i2, j1, j2 in group:
            if op == "equal":
                out += [Row("ctx", i1 + k + 1, j1 + k + 1, [(a[i1 + k], False)]) for k in range(i2 - i1)]
                continue
            paired = op == "replace" and (i2 - i1) == (j2 - j1)
            for k in range(i1, i2):
                spans = _spans(a[k], b[j1 + k - i1], "old") if paired else [(a[k], False)]
                out.append(Row("del", k + 1, None, spans))
                dels += 1
            for k in range(j1, j2):
                spans = _spans(a[i1 + k - j1], b[k], "new") if paired else [(b[k], False)]
                out.append(Row("add", None, k + 1, spans))
                adds += 1
    if len(out) > limit:
        more = len(out) - limit
        out = [*out[:limit], Row("gap", spans=[(f"… {more} more lines", False)])]
    return out, adds, dels


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"(\W)", s) if w]


def _spans(old: str, new: str, side: str) -> list[tuple[str, bool]]:
    """One side of a replaced line, split into pieces, each marked if it differs from the
    other side. Unchanged lines elsewhere give one unmarked piece."""
    wa, wb = _words(old), _words(new)
    out: list[tuple[str, bool]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        piece = "".join(wa[i1:i2] if side == "old" else wb[j1:j2])
        if piece:
            out.append((piece, op != "equal"))
    return out or [("", False)]


def listing(text: str, limit: int = MAX_ROWS) -> list[Row]:
    """A new file shown as it is: numbered lines, nothing marked as added (a diff of a new
    file would be one wall of +)."""
    lines = text.splitlines()
    out = [Row("ctx", None, i + 1, [(line, False)]) for i, line in enumerate(lines[:limit])]
    if len(lines) > limit:
        out.append(Row("gap", spans=[(f"… {len(lines) - limit} more lines", False)]))
    return out

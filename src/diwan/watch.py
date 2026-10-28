"""What changed outside the conversation since the session started: skills and memories.

The system prompt is built once (cache), from the skills and the memory index as they were at
the start. When they change during the session (a skill installed, a memory saved by another
session or machine), the model is told with a reminder at the start of the next turn, added at
the end of the conversation like any message, so nothing already sent changes.

The baseline is what the system prompt shows plus the reminders already given. A /rewind keeps
the reminders before the point it goes back to and drops the later ones, so the baseline goes
back to the last reminder still on the branch. A summary drops the reminders it covers, so it
puts the baseline back to the start: the next turn tells the whole difference again (at worst
a line the model has seen). On a resume nothing is lost: the prompt is built again from the
files as they are, and so is the baseline."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import memory, skills
from .paths import PathPolicy

MAX_LINES = 20   # lines of one reminder; the rest is counted


class Watch:
    def __init__(self, cwd: Path, mem: memory.Memory | None, paths: PathPolicy | None = None,
                 home: Path | None = None):
        self.cwd, self.mem, self.paths, self.home = cwd, mem, paths, home
        self.start_skills = self.skills = {s.name: s for s in skills.current}
        self.start_files = self.files = self._memory_files()
        # each reminder given: its log event, and the baseline right after it
        self.given: list[tuple[str, dict[str, skills.Skill], dict[str, str]]] = []

    def changes(self) -> str | None:
        """A reminder for what changed since the baseline, or None. Moves the baseline."""
        parts = [p for p in (self._skill_changes(), self._memory_changes()) if p]
        return "\n\n".join(parts) or None

    def given_as(self, event: str) -> None:
        """The reminder from changes() was logged as `event`."""
        self.given.append((event, self.skills, self.files))

    def rewound(self, branch: set[str]) -> None:
        """After a /rewind: the baseline of the last reminder still on the branch (`branch`: its
        event ids), else the start."""
        self.given = [g for g in self.given if g[0] in branch]
        self.skills, self.files = ((self.given[-1][1], self.given[-1][2]) if self.given
                                   else (self.start_skills, self.start_files))

    def forget(self) -> None:
        """After a summary: the reminders it covered are gone, so the next one tells everything
        that differs from the start (at worst, a line the model has seen)."""
        self.given = []
        self.skills, self.files = self.start_skills, self.start_files

    def own_save(self) -> None:
        """The session saved a memory itself: it knows, no reminder needed."""
        self.files = self._memory_files()

    # --- skills -----------------------------------------------------------------------------

    def _skill_changes(self) -> str | None:
        now = {s.name: s for s in skills.find(self.cwd, self.home)}
        if {n: s.description for n, s in now.items()} == \
                {n: s.description for n, s in self.skills.items()}:
            return None
        lines = [f"+ {n}: {s.description}" for n, s in now.items() if n not in self.skills]
        lines += [f"~ {n}: {s.description}" for n, s in now.items()
                  if n in self.skills and self.skills[n].description != s.description]
        lines += [f"- {n} (removed)" for n in self.skills if n not in now]
        self.skills = now
        skills.current = list(now.values())          # what the `skill` tool can load
        if self.paths is not None:                   # their files are read without asking
            self.paths.readable += [p.resolve() for p in skills.readable(skills.current)
                                    if p.resolve() not in self.paths.readable]
        return "Skills changed since this session started (load one with `skill`):\n" + clip(lines)

    # --- memory -----------------------------------------------------------------------------

    def _memory_files(self) -> dict[str, str]:
        """Each memory file, by its path in the memory folder, with a hash of its content."""
        if self.mem is None:
            return {}
        out = {}
        for place in [self.mem.folder, *(self.mem.folder / b for b in self.mem.bundles)]:
            for f in sorted(place.glob("*.md")) if place.is_dir() else []:
                if f.name != "MEMORY.md":
                    out[str(f.relative_to(self.mem.folder))] = hashlib.sha1(f.read_bytes()).hexdigest()
        return out

    def _memory_changes(self) -> str | None:
        if self.mem is None:
            return None
        now = self._memory_files()
        if now == self.files:
            return None
        lines = [f"+ {describe(self.mem.folder / p)} ({p})" for p in now
                 if now[p] != self.files.get(p)]
        lines += [f"- {p} (removed)" for p in self.files if p not in now]
        self.files = now
        return ("Memories added or changed since this session started (read one when it looks "
                f"relevant; folder {self.mem.folder}):\n" + clip(lines))


def describe(f: Path) -> str:
    """A memory's name and description, from its front matter."""
    try:
        head = f.read_text(encoding="utf-8")[:2000]
    except (OSError, UnicodeDecodeError):
        return f.stem
    name = re.search(r"^name:\s*(.+)$", head, re.M)
    desc = re.search(r"^description:\s*(.+)$", head, re.M)
    text = (name.group(1).strip() if name else f.stem)
    return text + (f": {desc.group(1).strip().strip(chr(34))}" if desc else "")


def clip(lines: list[str]) -> str:
    more = len(lines) - MAX_LINES
    return "\n".join(lines[:MAX_LINES] + ([f"... and {more} more"] if more > 0 else []))

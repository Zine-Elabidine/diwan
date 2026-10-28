"""What changed outside the conversation since the session started: skills, memories, MCP
servers, and the files the model read.

The system prompt is built once (cache), from the skills, the memory index and the MCP servers
as they were at the start. When they change during the session (a skill installed, a memory
saved by another session or machine, a server added to mcp.json), the model is told with a
reminder before its next request, added at the end of the conversation like any message, so
nothing already sent changes.

Each kind of change is a Check: `snapshot()` says what is there now, `note(before, now)` the
lines that tell the difference (the shape of Codex's world-state sections). Watch keeps, for
each check, what the model knows: the start, plus the reminders already given. A /rewind
keeps the reminders before the point it goes back to and drops the later ones, so it goes back
to the last reminder still on the branch. A summary drops the reminders it covers, so it puts
everything back to the start: the next reminder tells the whole difference again (at worst a
line the model has seen). On a resume nothing is lost: the prompt is built again from things
as they are, and so is the start."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from . import diff, mcp, memory, skills
from .paths import PathPolicy
from .tools import Tool
from .tools.files import digest

MAX_LINES = 20   # lines of one part of a reminder; the rest is counted
FILE_LINES = 30  # diff lines shown per changed file
MAX_FILES = 10   # changed files shown in one reminder
WAIT = 10.0      # seconds to wait for servers just added to mcp.json (Claude Code waits too)


class Check:
    def snapshot(self) -> Any:
        """What is there now, as plain data to compare."""
        raise NotImplementedError

    def note(self, before: Any, now: Any) -> str:
        """The lines that tell the model the difference (they differ)."""
        raise NotImplementedError


class Watch:
    def __init__(self, cwd: Path, mem: memory.Memory | None, paths: PathPolicy | None = None,
                 home: Path | None = None, tools: dict[str, Tool] | None = None):
        self.memory = Memories(mem)
        self.checks: list[Check] = [Skills(cwd, paths, home), self.memory]
        if tools is not None:
            self.checks.append(Servers(tools))
        self.start = self.known = [c.snapshot() for c in self.checks]
        # each reminder given: its log event, and what the model knew right after it
        self.given: list[tuple[str, list[Any]]] = []

    def changes(self) -> str | None:
        """A reminder for what changed since the model was last told, or None."""
        now = [c.snapshot() for c in self.checks]
        parts = [c.note(b, n) for c, b, n in zip(self.checks, self.known, now, strict=True)
                 if b != n]
        self.known = now
        return "\n\n".join(parts) or None

    def add(self, check: Check) -> None:
        """One more check, from now on (the agent adds the one for the files it reads)."""
        self.checks.append(check)
        snap = check.snapshot()
        self.start, self.known = [*self.start, snap], [*self.known, snap]
        self.given = [(e, [*known, snap]) for e, known in self.given]

    def given_as(self, event: str) -> None:
        """The reminder from changes() was logged as `event`."""
        self.given.append((event, self.known))

    def rewound(self, branch: set[str]) -> None:
        """After a /rewind: what the last reminder still on the branch (`branch`: its event
        ids) told, else the start."""
        self.given = [g for g in self.given if g[0] in branch]
        self.known = self.given[-1][1] if self.given else self.start

    def forget(self) -> None:
        """After a summary: the reminders it covered are gone, so the next one tells everything
        that differs from the start (at worst, a line the model has seen)."""
        self.given, self.known = [], self.start

    def own_save(self) -> None:
        """The session saved a memory itself: it knows, no reminder needed."""
        i = self.checks.index(self.memory)
        self.known = [*self.known[:i], self.memory.snapshot(), *self.known[i + 1:]]


# --- the checks -------------------------------------------------------------------------------

class Skills(Check):
    """Skills installed, changed or removed. Keeps what the `skill` tool can load up to date,
    and lets the model read new skills' files without asking."""

    def __init__(self, cwd: Path, paths: PathPolicy | None, home: Path | None):
        self.cwd, self.paths, self.home = cwd, paths, home

    def snapshot(self) -> dict[str, str]:
        found = skills.find(self.cwd, self.home)
        if found != skills.current:
            skills.current = found
            if self.paths is not None:
                self.paths.readable += [p.resolve() for p in skills.readable(found)
                                        if p.resolve() not in self.paths.readable]
        return {s.name: s.description for s in found}

    def note(self, before: dict[str, str], now: dict[str, str]) -> str:
        lines = [f"+ {n}: {d}" for n, d in now.items() if n not in before]
        lines += [f"~ {n}: {d}" for n, d in now.items() if n in before and before[n] != d]
        lines += [f"- {n} (removed)" for n in before if n not in now]
        return "Skills changed since this session started (load one with `skill`):\n" + clip(lines)


class Memories(Check):
    """Memory files added, changed or removed (by content, so a sync that only touches files
    says nothing)."""

    def __init__(self, mem: memory.Memory | None):
        self.mem = mem

    def snapshot(self) -> dict[str, str]:
        if self.mem is None:
            return {}
        out = {}
        for place in [self.mem.folder, *(self.mem.folder / b for b in self.mem.bundles)]:
            for f in sorted(place.glob("*.md")) if place.is_dir() else []:
                if f.name != "MEMORY.md":
                    out[str(f.relative_to(self.mem.folder))] = hashlib.sha1(f.read_bytes()).hexdigest()
        return out

    def note(self, before: dict[str, str], now: dict[str, str]) -> str:
        assert self.mem is not None
        lines = [f"+ {describe(self.mem.folder / p)} ({p})" for p in now if now[p] != before.get(p)]
        lines += [f"- {p} (removed)" for p in before if p not in now]
        return ("Memories added or changed since this session started (read one when it looks "
                f"relevant; folder {self.mem.folder}):\n" + clip(lines))


class Servers(Check):
    """MCP servers added to or removed from mcp.json, and ones that failed. Their tools join
    this session's as deferred ones, so the tools sent don't change (the cache holds) until the
    model loads them with tool_search: what Claude Code does with servers that connect late.
    The note lists their names."""

    def __init__(self, tools: dict[str, Tool]):
        self.tools = tools   # the agent's own dict: kept in step with the running servers

    def snapshot(self) -> dict[str, list[str]]:
        c = mcp.current
        if c is None:
            return {}
        if c.reload_if_changed():
            c.wait(WAIT)   # a new server is usually up in a moment: tell the model now, not later
        with c._lock:
            running = dict(c.tools)
            servers = list(c.servers)
            errors = dict(c.errors)
        for n in [n for n, t in self.tools.items()
                  if isinstance(t, mcp.MCPTool) and running.get(n) is not t]:
            del self.tools[n]
        self.tools.update({n: t for n, t in running.items() if n not in self.tools})
        out = {s.name: ["up", mcp.about(s), *mcp.names(s, running)] for s in servers}
        out.update({name: ["failed", why] for name, why in errors.items()})
        return out

    def note(self, before: dict[str, list[str]], now: dict[str, list[str]]) -> str:
        lines = [f"✗ {n} failed: {v[1]}" if v[0] == "failed" else mcp.listing(n, v[1], v[2:], "+")
                 for n, v in now.items() if before.get(n) != v]
        lines += [f"- {n} (removed)" for n in before if n not in now]
        return ("MCP servers changed since this session started (full tool names: "
                "mcp__<server>__<tool>; load one with `tool_search`):\n"
                + clip(lines))


class Files(Check):
    """Files the model read (or wrote) that changed on disk since: edited by the user, by a
    command (its own bash too), or by another program. Told with the lines that changed, as
    Claude Code does; `write` and `edit` still refuse them until they are read again."""

    def __init__(self, project: Path, seen: dict[Path, str], texts: dict[Path, str | None]):
        self.project, self.seen, self.texts = project, seen, texts

    def snapshot(self) -> dict[str, str]:
        """Each file whose content differs from what the model last saw: its new hash."""
        out = {}
        for p, seen in list(self.seen.items()):
            try:
                now = digest(p) if p.is_file() else "deleted"
            except OSError:
                now = "unreadable"
            if now != seen:
                out[str(p)] = now
        return out

    def note(self, before: dict[str, str], now: dict[str, str]) -> str:
        parts = []
        for path, h in now.items():
            if before.get(path) == h:
                continue
            p = Path(path)
            name = str(p.relative_to(self.project)) if p.is_relative_to(self.project) else path
            if h in ("deleted", "unreadable"):
                parts.append(f"{name}: {h}")
                continue
            old = self.texts.get(p)
            try:
                new = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                new = None
            if old is None or new is None:
                parts.append(f"{name}: changed (too big to show)")
                continue
            rows, adds, dels = diff.rows(old, new, context=1)
            lines = [diff_line(r) for r in rows]
            more = len(lines) - FILE_LINES
            lines = lines[:FILE_LINES] + ([f"... and {more} more lines"] if more > 0 else [])
            parts.append(f"{name} (+{adds} -{dels}):\n" + "\n".join(lines))
        return ("Files you read changed on disk since (by the user, a command or another program). "
                "Take this as their current state; read a file again before changing it:\n\n"
                + "\n\n".join(parts[:MAX_FILES])
                + (f"\n\n... and {len(parts) - MAX_FILES} more files" if len(parts) > MAX_FILES else ""))


def diff_line(r: diff.Row) -> str:
    if r.kind == "gap":
        return "  ..."
    mark, number = {"add": ("+", r.new), "del": ("-", r.old)}.get(r.kind, (" ", r.new))
    return f"{number or '':>5} {mark} {r.text}"


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

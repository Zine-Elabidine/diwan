"""What changed outside the conversation since the session started: skills, memories, MCP
servers.

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

from . import mcp, memory, skills
from .paths import PathPolicy
from .tools import Tool

MAX_LINES = 20   # lines of one part of a reminder; the rest is counted
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
    model loads them with tool_search: what Claude Code does with servers that connect late."""

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
        out = {}
        for s in servers:
            n = sum(1 for t in running.values() if isinstance(t, mcp.MCPTool) and t.server is s)
            about = s.instructions.splitlines()[0][:mcp.SERVER_LINE_MAX] if s.instructions else ""
            out[s.name] = [f"{n} tool{'s' if n != 1 else ''}", about]
        out.update({name: ["failed", why] for name, why in errors.items()})
        return out

    def note(self, before: dict[str, list[str]], now: dict[str, list[str]]) -> str:
        lines = [f"✗ {n} failed: {about}" if count == "failed" else
                 f"+ {n} ({count})" + (f": {about}" if about else "")
                 for n, (count, about) in now.items() if before.get(n) != [count, about]]
        lines += [f"- {n} (removed)" for n in before if n not in now]
        return ("MCP servers changed since this session started (load a server's tools with "
                "`tool_search`):\n" + clip(lines))


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

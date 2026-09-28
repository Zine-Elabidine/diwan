"""The four tools of v0: read, write, edit, bash. Small on purpose."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tarjuman import Tool

MAX_OUTPUT = 30_000
MAX_LINE = 2_000


class ToolError(Exception):
    """A failure the model should see and can fix (bad path, ambiguous edit...)."""


@dataclass
class Spec:
    tool: Tool
    run: Callable[..., str]
    readonly: bool

    @property
    def name(self) -> str:
        return self.tool.name


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n[... {len(text) - MAX_OUTPUT} characters cut ...]\n\n{text[-half:]}"


def make_tools(cwd: Path) -> dict[str, Spec]:
    def resolve(path: str) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else cwd / p

    def read(path: str, offset: int = 1, limit: int = 2000) -> str:
        p = resolve(path)
        if not p.is_file():
            raise ToolError(f"{path}: no such file")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(offset, 1) - 1
        chunk = lines[start:start + limit]
        out = [f"{i:>6}\t{line[:MAX_LINE]}" for i, line in enumerate(chunk, start + 1)]
        if start + limit < len(lines):
            out.append(f"[{len(lines) - start - limit} more lines; use offset={start + limit + 1}]")
        return "\n".join(out) or "(empty file)"

    def write(path: str, content: str) -> str:
        p = resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        return f"{'Overwrote' if existed else 'Created'} {path} ({content.count(chr(10)) + 1} lines)"

    def edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
        p = resolve(path)
        if not p.is_file():
            raise ToolError(f"{path}: no such file")
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if not old or n == 0:
            raise ToolError("`old` not found in the file; read it again and copy the text exactly")
        if n > 1 and not replace_all:
            raise ToolError(f"`old` appears {n} times; add surrounding lines to make it unique, "
                            "or set replace_all")
        p.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1),
                     encoding="utf-8")
        return f"Edited {path} ({n if replace_all else 1} replacement{'s' if replace_all and n > 1 else ''})"

    def bash(command: str, timeout: int = 120) -> str:
        try:
            r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True,
                               timeout=timeout, errors="replace")
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
            raise ToolError(f"timed out after {timeout}s\n{_clip(partial)}")
        out = (r.stdout + (("\n" if r.stdout and r.stderr else "") + r.stderr if r.stderr else "")).rstrip()
        return _clip(f"{out}\n[exit code {r.returncode}]" if r.returncode else out or "(no output)")

    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required}

    specs = [
        Spec(Tool("read", "Read a text file. Returns numbered lines.", obj({
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "first line to read, from 1"},
            "limit": {"type": "integer", "description": "number of lines (default 2000)"}},
            ["path"])), read, True),
        Spec(Tool("write", "Create a file or overwrite it entirely. Prefer `edit` for changes "
                  "to existing files.", obj({
            "path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])),
            write, False),
        Spec(Tool("edit", "Replace exact text in a file. `old` must match exactly once "
                  "unless replace_all is set. Read the file first.", obj({
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
            "replace_all": {"type": "boolean"}}, ["path", "old", "new"])), edit, False),
        Spec(Tool("bash", "Run a shell command in the project directory. Use it to list and "
                  "search files, run tests, git, etc.", obj({
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds (default 120)"}},
            ["command"])), bash, False),
    ]
    return {s.name: s for s in specs}

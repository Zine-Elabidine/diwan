"""The four tools of v0: read, write, edit, bash. Small on purpose."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tarjuman import Cancel, Tool

MAX_OUTPUT = 30_000
MAX_LINE = 2_000


class ToolError(Exception):
    """A failure the model should see and can fix (bad path, ambiguous edit...)."""


@dataclass
class Spec:
    tool: Tool
    run: Callable[..., str]
    readonly: bool
    cancellable: bool = False  # run() takes the turn's `cancel` signal and stops when it fires

    @property
    def name(self) -> str:
        return self.tool.name


def find_shell() -> tuple[list[str], str]:
    """The command prefix to run a shell command, and the shell's name for the model.
    Windows: Git Bash if installed (never WSL's System32 bash), else PowerShell."""
    if os.name != "nt":
        return ([shutil.which("bash")], "bash") if shutil.which("bash") else (["/bin/sh"], "sh")
    candidates = []
    git = shutil.which("git")
    if git:  # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "Programs")):
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file():
            return [str(c)], "bash (Git Bash on Windows)"
    ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return [ps, "-NoProfile", "-NonInteractive"], "PowerShell"


def _kill_tree(p: subprocess.Popen[str]) -> None:
    """Stop a command and everything it started: SIGTERM to its process group, a short grace
    for cleanup, then SIGKILL to whatever is left (Codex and Gemini CLI do the same).
    Windows: taskkill /T /F on the tree."""
    if os.name == "nt":
        if p.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        p.wait(0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(p.pid, signal.SIGKILL)  # children can outlive the shell
    except (ProcessLookupError, PermissionError):
        pass


def _drain(p: subprocess.Popen[str]) -> tuple[str, str]:
    """What a stopped command printed. Gives up after 2 s: something that escaped the group
    may still hold the pipes open."""
    try:
        return p.communicate(timeout=2)
    except subprocess.TimeoutExpired as e:
        text = lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")
        return text(e.stdout), text(e.stderr)


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n[... {len(text) - MAX_OUTPUT} characters cut ...]\n\n{text[-half:]}"


def make_tools(cwd: Path) -> dict[str, Spec]:
    shell, shell_name = find_shell()
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

    def bash(command: str, timeout: int = 120, cancel: Cancel | None = None) -> str:
        flag = "-Command" if shell_name == "PowerShell" else "-c"
        start = time.monotonic()
        # its own process group, so stopping it stops everything it started; no stdin, so a
        # command that waits for input fails instead of hanging (or reading the user's keys)
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"start_new_session": True})
        p = subprocess.Popen([*shell, flag, command], cwd=cwd, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", **group)
        stopped = None
        try:
            while True:
                try:
                    out, err = p.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.cancelled:
                        stopped = f"aborted by user after {time.monotonic() - start:.1f}s"
                    elif time.monotonic() - start > timeout:
                        stopped = f"timed out after {timeout}s"
                    if stopped:
                        _kill_tree(p)
                        out, err = _drain(p)
                        break
        except BaseException:  # Ctrl+C in plain mode, or anything else: never leave it running
            _kill_tree(p)
            raise
        out = (out + (("\n" if out and err else "") + err if err else "")).rstrip()
        if stopped:
            raise ToolError(_clip(f"{stopped}\n{out}" if out else f"{stopped} (no output)"))
        return _clip(f"{out}\n[exit code {p.returncode}]" if p.returncode else out or "(no output)")

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
        Spec(Tool("bash", f"Run a shell command in the project directory with {shell_name}. "
                  "Use it to list and search files, run tests, git, etc.", obj({
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds (default 120)"}},
            ["command"])), bash, False, cancellable=True),
    ]
    return {s.name: s for s in specs}

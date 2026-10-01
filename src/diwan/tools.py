"""The tools: read, write, edit, bash, and grep/glob for search. Small on purpose."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tarjuman import Cancel, Tool

from .paths import Access, PathPolicy

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
    path_arg: str | None = None  # the argument holding a path, checked by the path policy

    @property
    def name(self) -> str:
        return self.tool.name


def find_shell() -> tuple[list[str], str]:
    """The command prefix to run a shell command, and the shell's name for the model.
    Windows: Git Bash if installed (never WSL's System32 bash), else PowerShell."""
    if os.name != "nt":
        bash = shutil.which("bash")
        return ([bash], "bash") if bash else (["/bin/sh"], "sh")
    candidates = []
    git = shutil.which("git")
    if git:  # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
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
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                           check=False)
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
        return _decode(e.stdout), _decode(e.stderr)


def _decode(b: bytes | str | None) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")


# heavy folders skipped when the project isn't a git repo (in a repo, .gitignore decides)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", ".tox", ".next", "dist", "build", "target"}
GREP_LIMIT = 100       # matching lines shown
GLOB_LIMIT = 200       # paths shown
MAX_MATCH_LINE = 300   # characters shown per matching line
MAX_FILE = 2_000_000   # bytes; bigger files are skipped by grep


def _files(root: Path) -> list[Path]:
    """The project's files under `root`: git's view when it is a repo (tracked and untracked,
    minus what .gitignore excludes), otherwise a walk that skips the usual heavy folders."""
    if root.is_file():
        return [root]
    try:
        r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           cwd=root, capture_output=True, timeout=30, check=False)
        if r.returncode == 0:
            paths = [root / p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]
            return [p for p in paths if p.is_file()]  # tracked files may be deleted
    except (OSError, subprocess.TimeoutExpired):
        pass
    out: list[Path] = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        out += [Path(d) / f for f in sorted(files)]
    return out


def _matches(rel: Path, pattern: str) -> bool:
    """A glob against the relative path; a pattern without "/" also matches the bare name,
    so "*.py" finds Python files at any depth."""
    return rel.full_match(pattern) or ("/" not in pattern and rel.full_match("**/" + pattern))


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n[... {len(text) - MAX_OUTPUT} characters cut ...]\n\n{text[-half:]}"


def access(spec: Spec, args: dict[str, Any], policy: PathPolicy) -> Access:
    """What a call needs: no question, the user's approval (outside the project), or refusal."""
    if spec.path_arg is None:
        return Access.INSIDE
    return policy.check(str(args.get(spec.path_arg) or "."))


def make_tools(cwd: Path, policy: PathPolicy | None = None) -> dict[str, Spec]:
    shell, shell_name = find_shell()
    policy = policy or PathPolicy(cwd)

    def resolve(path: str) -> Path:
        if policy.check(path) is Access.DENIED:   # the agent refuses first; this is the backstop
            raise ToolError(policy.why(path))
        return policy.resolve(path)

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
        group: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                                 if os.name == "nt" else {"start_new_session": True})
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

    def glob(pattern: str, path: str = ".") -> str:
        base = resolve(path)
        if not base.exists():
            raise ToolError(f"{path} does not exist")
        found = [f for f in _files(base) if _matches(f.relative_to(base), pattern)
                 and not policy.secret(f.resolve())]
        if not found:
            return f"No files match {pattern!r} under {path}"
        found.sort(key=lambda f: f.stat().st_mtime, reverse=True)  # recently changed first
        shown = [str(f.relative_to(cwd) if f.is_relative_to(cwd) else f) for f in found[:GLOB_LIMIT]]
        more = len(found) - len(shown)
        return "\n".join(shown) + (f"\n[{more} more; narrow the pattern]" if more else "")

    def grep(pattern: str, path: str = ".", glob: str | None = None, ignore_case: bool = False,
             context: int = 0) -> str:
        base = resolve(path)
        if not base.exists():
            raise ToolError(f"{path} does not exist")
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise ToolError(f"invalid regular expression: {e}") from None
        context = max(0, min(context, 10))
        out: list[str] = []
        hits = files_hit = 0
        for f in _files(base):
            if glob and not _matches(f.relative_to(base) if base.is_dir() else Path(f.name), glob):
                continue
            if f != base and not policy.searchable(f):   # secrets, .env files, links to them
                continue
            try:
                if f.stat().st_size > MAX_FILE:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8192]:
                continue  # binary
            lines = data.decode("utf-8", "replace").splitlines()
            idx = [i for i, line in enumerate(lines) if rx.search(line)]
            if not idx:
                continue
            files_hit += 1
            name = str(f.relative_to(cwd) if f.is_relative_to(cwd) else f)
            shown: set[int] = set()
            for i in idx:
                if hits >= GREP_LIMIT:
                    break
                hits += 1
                for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                    if j in shown:
                        continue
                    if context and shown and j - 1 not in shown:
                        out.append("--")
                    shown.add(j)
                    sep = ":" if j == i or j in idx else "-"
                    out.append(f"{name}{sep}{j + 1}{sep} {lines[j][:MAX_MATCH_LINE]}")
            if hits >= GREP_LIMIT:
                out.append(f"[stopped at {GREP_LIMIT} matches; narrow the pattern, path or glob]")
                break
        if not out:
            return f"No matches for {pattern!r}" + (f" in {glob}" if glob else "")
        return "\n".join(out)

    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required}

    specs = [
        Spec(Tool("read", "Read a text file. Returns numbered lines.", obj({
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "first line to read, from 1"},
            "limit": {"type": "integer", "description": "number of lines (default 2000)"}},
            ["path"])), read, True, path_arg="path"),
        Spec(Tool("grep", "Search file contents with a regular expression (Python syntax). Returns "
                  "path:line: text for each match. Skips binary files and what .gitignore "
                  "excludes. Use it instead of grep/rg in bash.", obj({
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "file or folder (default: the project)"},
            "glob": {"type": "string", "description": "only files matching, e.g. *.py or src/**/*.ts"},
            "ignore_case": {"type": "boolean"},
            "context": {"type": "integer", "description": "lines shown around each match (max 10)"}},
            ["pattern"])), grep, True, path_arg="path"),
        Spec(Tool("glob", "Find files by name pattern, e.g. **/*.py or src/**/test_*.py. A pattern "
                  "without / matches at any depth. Recently changed files first.", obj({
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "folder to search (default: the project)"}},
            ["pattern"])), glob, True, path_arg="path"),
        Spec(Tool("write", "Create a file or overwrite it entirely. Prefer `edit` for changes "
                  "to existing files.", obj({
            "path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])),
            write, False, path_arg="path"),
        Spec(Tool("edit", "Replace exact text in a file. `old` must match exactly once "
                  "unless replace_all is set. Read the file first.", obj({
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
            "replace_all": {"type": "boolean"}}, ["path", "old", "new"])), edit, False,
            path_arg="path"),
        Spec(Tool("bash", f"Run a shell command in the project directory with {shell_name}. "
                  "Use it to run tests, builds, git and programs; use grep, glob and read to "
                  "search and read files.", obj({
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds (default 120)"}},
            ["command"])), bash, False, cancellable=True),
    ]
    return {s.name: s for s in specs}

"""Searching the project: grep (contents) and glob (names). Pure Python, so they work the same
everywhere; in a git repo, git decides which files belong to the project."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .base import Tool, ToolContext, ToolError, schema

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


class Grep(Tool):
    name = "grep"
    description = ("Search file contents with a regular expression (Python syntax). Returns "
                   "path:line: text for each match. Skips binary files and what .gitignore "
                   "excludes. Use it instead of grep/rg in bash.")
    parameters = schema({
        "pattern": {"type": "string"},
        "path": {"type": "string", "description": "file or folder (default: the project)"},
        "glob": {"type": "string", "description": "only files matching, e.g. *.py or src/**/*.ts"},
        "ignore_case": {"type": "boolean"},
        "context": {"type": "integer", "description": "lines shown around each match (max 10)"}},
        ["pattern"])
    readonly = True
    path_arg = "path"

    def run(self, ctx: ToolContext, pattern: str, path: str = ".", glob: str | None = None,
            ignore_case: bool = False, context: int = 0) -> str:
        base = ctx.resolve(path)
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
            if f != base and not ctx.paths.searchable(f):   # secrets, .env files, links to them
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
            name = str(f.relative_to(ctx.cwd) if f.is_relative_to(ctx.cwd) else f)
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


class Glob(Tool):
    name = "glob"
    description = ("Find files by name pattern, e.g. **/*.py or src/**/test_*.py. A pattern "
                   "without / matches at any depth. Recently changed files first.")
    parameters = schema({
        "pattern": {"type": "string"},
        "path": {"type": "string", "description": "folder to search (default: the project)"}},
        ["pattern"])
    readonly = True
    path_arg = "path"

    def run(self, ctx: ToolContext, pattern: str, path: str = ".") -> str:
        base = ctx.resolve(path)
        if not base.exists():
            raise ToolError(f"{path} does not exist")
        found = [f for f in _files(base) if _matches(f.relative_to(base), pattern)
                 and not ctx.paths.secret(f.resolve())]
        if not found:
            return f"No files match {pattern!r} under {path}"
        found.sort(key=lambda f: f.stat().st_mtime, reverse=True)  # recently changed first
        shown = [str(f.relative_to(ctx.cwd) if f.is_relative_to(ctx.cwd) else f) for f in found[:GLOB_LIMIT]]
        more = len(found) - len(shown)
        return "\n".join(shown) + (f"\n[{more} more; narrow the pattern]" if more else "")

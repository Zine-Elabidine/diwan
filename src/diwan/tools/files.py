"""Reading and changing files: read, write, edit."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolError, schema

MAX_LINE = 2_000


class Read(Tool):
    name = "read"
    description = ("Read a text file. Returns numbered lines.")
    parameters = schema({
        "path": {"type": "string"},
        "offset": {"type": "integer", "description": "first line to read, from 1"},
        "limit": {"type": "integer", "description": "number of lines (default 2000)"}},
        ["path"])
    readonly = True
    path_arg = "path"

    def run(self, ctx: ToolContext, path: str, offset: int = 1, limit: int = 2000) -> str:
        p = ctx.resolve(path)
        if not p.is_file():
            raise ToolError(f"{path}: no such file")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(offset, 1) - 1
        chunk = lines[start:start + limit]
        out = [f"{i:>6}\t{line[:MAX_LINE]}" for i, line in enumerate(chunk, start + 1)]
        if start + limit < len(lines):
            out.append(f"[{len(lines) - start - limit} more lines; use offset={start + limit + 1}]")
        return "\n".join(out) or "(empty file)"


class Write(Tool):
    name = "write"
    description = ("Create a file or overwrite it entirely. Prefer `edit` for changes "
                   "to existing files.")
    parameters = schema({
        "path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"])
    path_arg = "path"

    def run(self, ctx: ToolContext, path: str, content: str) -> str:
        p = ctx.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        return f"{'Overwrote' if existed else 'Created'} {path} ({content.count(chr(10)) + 1} lines)"


class Edit(Tool):
    name = "edit"
    description = ("Replace exact text in a file. `old` must match exactly once "
                   "unless replace_all is set. Read the file first.")
    parameters = schema({
        "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
        "replace_all": {"type": "boolean"}},
        ["path", "old", "new"])
    path_arg = "path"

    def run(self, ctx: ToolContext, path: str, old: str, new: str, replace_all: bool = False) -> str:
        p = ctx.resolve(path)
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

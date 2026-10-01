"""The tools the model can call. Each is a Tool (base.py): its name, description and JSON schema
for the model, whether it only reads, which argument holds a path, and `run(ctx, **args)`.
A new tool is a new class here and one line in `default_tools`."""

from .base import Tool, ToolContext, ToolError, access
from .files import Edit, Read, Write
from .search import Glob, Grep
from .shell import Bash, find_shell


def default_tools() -> dict[str, Tool]:
    """Every tool, in the order the model is told about them (kept stable for the cache)."""
    return {t.name: t for t in (Read(), Grep(), Glob(), Write(), Edit(), Bash())}


__all__ = ["Bash", "Edit", "Glob", "Grep", "Read", "Tool", "ToolContext", "ToolError", "Write",
           "access", "default_tools", "find_shell"]

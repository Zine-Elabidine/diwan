"""What every tool is, and what every call gets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tarjuman
from tarjuman import Cancel

from ..paths import Access, PathPolicy

MAX_OUTPUT = 30_000


class ToolError(Exception):
    """A failure the model should see and can fix (bad path, ambiguous edit...)."""


@dataclass
class ToolContext:
    """What a call may need beyond its arguments. New capabilities (a project index, a
    sub-agent handle) are added here, not to each tool's signature."""
    paths: PathPolicy
    cancel: Cancel = field(default_factory=Cancel)   # the running turn's stop signal

    @property
    def cwd(self) -> Path:
        return self.paths.project

    def resolve(self, path: str) -> Path:
        if self.paths.check(path) is Access.DENIED:   # the agent refuses first; this is the backstop
            raise ToolError(self.paths.why(path))
        return self.paths.resolve(path)


class Tool:
    """A tool the model can call. `run(ctx, **args)` takes exactly the properties of
    `parameters` (tests/test_tools.py checks they match) and returns what the model reads."""
    name: str
    description: str
    parameters: dict[str, Any]       # JSON schema of the arguments
    readonly: bool = False           # runs without approval inside the project
    path_arg: str | None = None      # the argument holding a path, checked by the path policy
    run: Callable[..., str]

    @property
    def definition(self) -> tarjuman.Tool:
        """What the model is told about it."""
        return tarjuman.Tool(self.name, self.description, self.parameters)


def schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


def access(tool: Tool, args: dict[str, Any], policy: PathPolicy) -> Access:
    """What a call needs: no question, the user's approval (outside the project), or refusal."""
    if tool.path_arg is None:
        return Access.INSIDE
    return policy.check(str(args.get(tool.path_arg) or "."))


def clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n[... {len(text) - MAX_OUTPUT} characters cut ...]\n\n{text[-half:]}"

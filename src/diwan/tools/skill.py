"""skill: load a skill's instructions (its SKILL.md) when a task matches it."""

from __future__ import annotations

from .. import skills
from .base import Tool, ToolContext, ToolError, schema


class Skill(Tool):
    name = "skill"
    description = ("Load a skill: instructions for a kind of task, listed under Skills in your "
                   "instructions. Load it before starting a task it matches, then follow it.")
    parameters = schema({"name": {"type": "string", "description": "the skill's name, as listed"}},
                        ["name"])
    readonly = True

    def run(self, ctx: ToolContext, name: str) -> str:
        found = {s.name: s for s in skills.current}
        s = found.get(name.strip())
        if s is None:
            raise ToolError(f"no skill named {name!r}. Skills: {', '.join(found) or '(none)'}")
        try:
            text = skills.body(s)
        except (OSError, UnicodeDecodeError) as e:
            raise ToolError(f"can't read {s.path}: {e}") from None
        return (f"Skill {s.name}. Its folder is {s.path.parent}: read the other files there when "
                f"it says so (no approval needed).\n\n{text}")

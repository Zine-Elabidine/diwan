"""Handing a task to a fresh agent: docs/design-decisions.md §12. The child starts with an empty
conversation (only the task), works with the same model and tools minus this one (depth 1),
asks for the same approvals, and keeps its own session log. The parent sees the call and the
child's final answer, so a long search costs the parent's context only its result."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolError, schema


class Agent(Tool):
    name = "agent"
    description = ("Hand a self-contained task to a fresh agent with an empty context, and get "
                   "its final answer back. Use it for work whose steps you don't need to see: "
                   "searching a large codebase, investigating a question, a review. The agent "
                   "knows only what you write in `task`: give it the goal, the paths and "
                   "names you already know, and what its answer must contain. readonly: true "
                   "for research (it can only read, search and take notes), false when it "
                   "must change files or run commands.")
    parameters = schema({
        "task": {"type": "string", "description": "complete instructions for the agent"},
        "readonly": {"type": "boolean", "description": "only read, search and note"}},
        ["task", "readonly"])
    readonly = True   # starting it changes nothing; its own calls ask for approval as usual

    def run(self, ctx: ToolContext, task: str, readonly: bool) -> str:
        if ctx.spawn is None:
            raise ToolError("agents can't start agents")
        if not task.strip():
            raise ToolError("the task is empty")
        return ctx.spawn(task, readonly)

"""Handing a task to another agent: docs/design-decisions.md §12. The child starts either fresh
(only the task) or as a fork (a copy of the conversation so far, sent exactly as the parent
last sent it, so the provider's cache covers it). It works with the same model, can't start
agents itself (depth 1), asks for the same approvals, and keeps its own session log. The
parent sees the call and the child's final answer, so a long search costs the parent's context
only its result."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolError, schema


class Agent(Tool):
    name = "agent"
    description = ("Hand a task to another agent and get its final answer back. Use it for work "
                   "whose steps you don't need to see: searching a large codebase, "
                   "investigating a question, a review, trying an approach. Several calls in "
                   "one reply run at the same time when they are read-only. fork: false starts "
                   "it with an empty context: it knows only what you write in `task` (give it "
                   "the goal, the paths and names you know, what its answer must contain). "
                   "fork: true starts it with a copy of this conversation, so `task` can be "
                   "short; prefer it when the task depends on what was said here. readonly: "
                   "true for research (it can only read, search and take notes), false when it "
                   "must change files or run commands.")
    parameters = schema({
        "task": {"type": "string", "description": "the instructions for the agent"},
        "readonly": {"type": "boolean", "description": "only read, search and note"},
        "fork": {"type": "boolean", "description": "start with a copy of this conversation"}},
        ["task", "readonly"])
    readonly = True   # starting it changes nothing; its own calls ask for approval as usual

    def run(self, ctx: ToolContext, task: str, readonly: bool, fork: bool = False) -> str:
        if ctx.spawn is None:
            raise ToolError("agents can't start agents")
        if not task.strip():
            raise ToolError("the task is empty")
        return ctx.spawn(task, readonly, fork)

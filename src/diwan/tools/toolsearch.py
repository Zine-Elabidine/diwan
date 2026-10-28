"""tool_search: load deferred tools (MCP servers' tools) when the model needs them.

Deferred tools are left out of requests; the model sees only their servers (mcp.prompt_section).
It searches here, gets the full definitions back, and from the next step on they are sent like
any other tool (Agent.definitions). Loading is a log event, so a resumed session keeps them."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import Tool, ToolContext, ToolError, schema

LIMIT = 5   # tools returned by a keyword search
SUGGEST = 30   # names listed when `select:` names a tool that doesn't exist


class ToolSearch(Tool):
    name = "tool_search"
    description = (
        "Load tools that are not loaded yet (the MCP servers' tools listed in your instructions). "
        "`query` is either keywords, matched against tool names and descriptions (the best "
        f"{LIMIT} are loaded), or `select:<name>,<name>` to load exact tools. Returns their full "
        "definitions; you can call them from your next step on.")
    parameters = schema({"query": {"type": "string",
                                   "description": "keywords, or select:<name>,<name>"}}, ["query"])
    readonly = True   # loads tools, changes nothing outside the session

    def run(self, ctx: ToolContext, query: str) -> str:
        if not ctx.deferred or ctx.load is None:
            raise ToolError("no tools to load in this session")
        query = query.strip()
        if query.startswith("select:"):
            names = [n.strip() for n in query[len("select:"):].split(",") if n.strip()]
            unknown = [n for n in names if n not in ctx.deferred]
            if unknown or not names:
                close = [n for n in ctx.deferred if any(u.lower() in n.lower() for u in unknown)]
                raise ToolError(f"no such tool: {', '.join(unknown) or '(none named)'}. " + (
                    f"Tools with that in their name: {', '.join(close[:SUGGEST])}." if close else
                    "Search with keywords to find the exact name."))
        else:
            names = rank(query, ctx.deferred)[:LIMIT]
            if not names:
                return (f"No tool matches {query!r}. Try other words (an action, or what it acts "
                        "on), or a server's name.")
        ctx.load(names)
        return (f"Loaded {len(names)} tool{'s' if len(names) != 1 else ''}, callable from your "
                f"next step:\n\n" + show([ctx.deferred[n] for n in names]))


def words(text: str) -> set[str]:
    """Lower-case words, split on anything not a letter or digit (so `create_issue` gives two),
    with a plural `s` dropped so "issues" finds "issue"."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def rank(query: str, tools: dict[str, Tool]) -> list[str]:
    """The tools matching any query word, best first: a word in the name counts twice as much
    as one in the description. Ties keep the servers' own order."""
    wanted = words(query)
    scored = []
    for i, (name, t) in enumerate(tools.items()):
        score = 2 * len(wanted & words(name)) + len(wanted & words(t.description))
        if score:
            scored.append((-score, i, name))
    return [name for _, _, name in sorted(scored)]


def show(tools: list[Tool]) -> str:
    """What the model reads back: each loaded tool's name, description and parameters."""
    return "\n\n".join(json.dumps(definition(t), ensure_ascii=False) for t in tools)


def definition(t: Tool) -> dict[str, Any]:
    return {"name": t.name, "description": t.description, "parameters": t.parameters}

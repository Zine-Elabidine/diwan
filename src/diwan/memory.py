"""Memory across sessions and machines, kept by Telepathy (docs/design-decisions.md §13).

At the start, `tp session start` pulls the user's memory store and says where this project's
memory folder is, with its index. The index goes into the system prompt once, as it was at
the start (a frozen snapshot: the prompt never changes mid-session, so the cache holds). The
model reads memory files with the usual tools (no approval needed there) and saves new ones
with the `memory` tool, which writes the file into the right bundle. At the end,
`tp session end` files them, rebuilds the indexes, commits, and pushes in the background.

Without `tp`, or for a project that loads no bundles on this machine, there is no memory."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tools.base import Tool, ToolContext, ToolError, schema

TIMEOUT = 30        # seconds for `tp session start|end` (a pull over the network)
TYPES = ("user", "feedback", "project", "reference")
PERSONAL_TYPES = ("user", "feedback")   # these go to the personal bundle, the rest to `write`


@dataclass
class Memory:
    folder: Path            # ~/.telepathy/sessions/<project>: one link per bundle, and MEMORY.md
    index: str              # MEMORY.md as it was at the start
    bundles: list[str]
    write: str              # the bundle for project and reference memories
    personal: str | None    # the bundle for user and feedback memories

    def bundle_for(self, kind: str) -> str:
        return self.personal if kind in PERSONAL_TYPES and self.personal else self.write

    def readable(self) -> list[Path]:
        """Where memory files really are (the bundle links resolved): read without asking."""
        places = [self.folder.resolve()]
        places += [(self.folder / b).resolve() for b in self.bundles if (self.folder / b).exists()]
        return places


# the memory this process loaded (cli.py), for the prompt, the tool and /memory
current: Memory | None = None


def start(cwd: Path) -> tuple[Memory | None, str]:
    """This project's memory, and a message for the user (why there is none, or "")."""
    tp = shutil.which("tp")
    if tp is None:
        return None, ""
    try:
        r = subprocess.run([tp, "session", "start", "--cwd", str(cwd)], capture_output=True,
                           text=True, encoding="utf-8", timeout=TIMEOUT, check=False)
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return None, f"memory: tp session start failed ({type(e).__name__})"
    if not data.get("folder"):
        return None, data.get("message") or ""
    return Memory(Path(data["folder"]), data.get("index") or "", list(data.get("bundles") or []),
                  data.get("write") or "", data.get("personal")), ""


def end(cwd: Path) -> None:
    """File, index, commit and push what this session saved (the push runs in the
    background, so quitting never waits on the network)."""
    tp = shutil.which("tp")
    if tp is None:
        return
    try:
        subprocess.run([tp, "session", "end", "--cwd", str(cwd)], capture_output=True,
                       timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


PROMPT = """

# Memory

You have a memory that lasts across sessions and machines, in {folder}. Its index, as it was
when this session started:

{index}

Read a memory file (with `read`) when it looks relevant to the task. Save with `memory` when
you learn something a later session needs: who the user is and how they like to work (user,
feedback: include why, and how to apply it), or what the project's code and history don't
show (project: goals, constraints, decisions; reference: where things are). Don't save what
the code, git history or project instructions already say, or what only matters now. Saving
under a name that exists replaces that memory: update it rather than adding a near copy."""


def prompt_section(m: Memory) -> str:
    return PROMPT.format(folder=m.folder, index=m.index.strip())


class Remember(Tool):
    name = "memory"
    description = ("Save a memory for later sessions (on every machine of the user). One fact "
                   "per memory. Saving under an existing name replaces it.")
    parameters = schema({
        "name": {"type": "string", "description": "short-kebab-case-slug, e.g. prefers-small-commits"},
        "type": {"type": "string", "enum": list(TYPES)},
        "description": {"type": "string", "description": "one line, used to decide relevance later"},
        "text": {"type": "string", "description": "the memory; for feedback and project, "
                                                  "follow with **Why:** and **How to apply:**"}},
        ["name", "type", "description", "text"])

    def run(self, ctx: ToolContext, name: str, type: str, description: str, text: str) -> str:
        m = current
        if m is None:
            raise ToolError("no memory for this project (Telepathy is not set up here)")
        if type not in TYPES:
            raise ToolError(f"type must be one of {', '.join(TYPES)}")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 60:
            raise ToolError("name must be a short kebab-case slug (a-z, 0-9, -)")
        if not description.strip() or not text.strip():
            raise ToolError("description and text can't be empty")
        bundle = m.bundle_for(type)
        stem = name if name.startswith(f"{type}_") else f"{type}_{name}"
        path = m.folder / bundle / f"{stem}.md"
        existed = path.exists()
        one_line = " ".join(description.split())
        path.write_text(f"---\nname: {name}\ndescription: {json.dumps(one_line, ensure_ascii=False)}\n"
                        f"metadata:\n  type: {type}\n---\n\n{text.strip()}\n",
                        encoding="utf-8", newline="\n")
        return f"{'Updated' if existed else 'Saved'} {bundle}/{path.name}"


def describe(m: Memory | None) -> str:
    """For /memory."""
    if m is None:
        return ("No memory for this project. With Telepathy (`tp`), run `tp use <bundles>` in "
                "this folder to choose what it loads.")
    count = sum(1 for b in m.bundles for _ in (m.folder / b).glob("*.md")
                if _.name != "MEMORY.md")
    return (f"Memory: {m.folder}\nbundles: {', '.join(m.bundles)} ({count} memories); new "
            f"project memories go to {m.write}" + (f", personal ones to {m.personal}"
                                                   if m.personal else ""))

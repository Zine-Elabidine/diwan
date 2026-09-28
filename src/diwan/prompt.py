"""The system prompt: short, plus the project's own instructions if it has any."""

from __future__ import annotations

import platform
import time
from pathlib import Path

BASE = """You are Diwan, a coding agent working in the user's terminal, in the project at {cwd}.

You have four tools: read, write, edit and bash. Use them to look before you act: read files
before editing them, search with bash (rg, grep, find, ls), and run tests or the program to
check your work. Prefer `edit` over rewriting whole files.

Work until the task is done, then stop and give a short summary of what you changed and how
you checked it. If something is unclear or risky (deleting data, pushing, anything hard to
undo), ask first. If you could not verify something, say so plainly.

Keep replies concise. Refer to code as path:line.

Environment: {os}, today is {date}."""

PROJECT_FILES = ("AGENTS.md", "DIWAN.md", "CLAUDE.md")


def system_prompt(cwd: Path) -> str:
    text = BASE.format(cwd=cwd, os=f"{platform.system()} {platform.release()}",
                       date=time.strftime("%Y-%m-%d"))
    for name in PROJECT_FILES:
        p = cwd / name
        if p.is_file():
            text += f"\n\n# Project instructions ({name})\n\n{p.read_text(encoding='utf-8')[:20_000]}"
            break
    return text

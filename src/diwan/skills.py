"""Skills: instructions for particular tasks, kept in files (docs/design-decisions.md §11, the
"plain files" way). A skill is a folder with a SKILL.md that starts with a `name` and a
`description`, the format Claude Code uses, so its skills work here as they are.

Only the names and descriptions go into the system prompt (once, at the start). The model
reads a skill's SKILL.md, and any file it points to, when a task matches: a hundred skills
cost a few lines each until one is needed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DESCRIPTION_MAX = 300   # characters of a description in the prompt


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path          # its SKILL.md


# the skills this process found at its start (cli.py), for the prompt and /skills
current: list[Skill] = []


def places(project: Path, home: Path | None = None) -> list[Path]:
    """Where skills live, the project's first: the same name in a later place is ignored."""
    home = home or Path.home()
    return [project / ".diwan" / "skills", project / ".claude" / "skills",
            home / ".diwan" / "skills", home / ".claude" / "skills"]


def find(project: Path, home: Path | None = None) -> list[Skill]:
    found: dict[str, Skill] = {}
    for place in places(project, home):
        if not place.is_dir():
            continue
        for f in sorted(place.glob("*/SKILL.md")):
            s = read(f)
            if s is not None and s.name not in found:
                found[s.name] = s
    return list(found.values())


def read(path: Path) -> Skill | None:
    """The skill a SKILL.md describes, or None without a name and description."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None
    fields = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip("\"'")
    name, description = fields.get("name"), fields.get("description")
    if not name or not description:
        return None
    if len(description) > DESCRIPTION_MAX:
        description = description[:DESCRIPTION_MAX].rsplit(" ", 1)[0] + "..."
    return Skill(name, description, path)


def readable(skills: list[Skill]) -> list[Path]:
    """The skill folders, which the model may read without asking."""
    return [s.path.parent for s in skills]


PROMPT = """

# Skills

Instructions for particular kinds of task, in files. When a task matches a skill, read its
SKILL.md before starting, and follow it; read the other files it points to when it says so.
{lines}"""


def prompt_section(skills: list[Skill]) -> str:
    if not skills:
        return ""
    return PROMPT.format(lines="\n".join(f"- {s.name}: {s.description} ({s.path})"
                                         for s in skills))


def describe(skills: list[Skill]) -> str:
    """For /skills."""
    if not skills:
        return ("No skills. A skill is a folder with a SKILL.md (name and description at the "
                "top) in .diwan/skills or .claude/skills, in the project or your home folder.")
    return "\n".join(f"● {s.name}: {s.description}\n  {s.path}" for s in skills)

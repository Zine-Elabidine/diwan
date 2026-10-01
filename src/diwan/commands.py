"""The slash commands, written once for both front-ends. A command acts on the Session and
returns a Reply; the front-end shows it its own way and carries out the reply's effect
(quitting, showing reasoning, redrawing after /new). /help is generated from this list."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from tarjuman import TarjumanError

from .models import listing
from .present import fmt_notes, fmt_usage
from .session import Session

Effect = Literal["exit", "think", "new", "model"]


@dataclass
class Reply:
    text: str = ""
    kind: Literal["note", "error", "block"] = "note"   # block: belongs in the transcript
    effect: Effect | None = None


@dataclass(frozen=True)
class Command:
    name: str
    args: str
    help: str
    run: Callable[[Session, str], Reply]
    busy_ok: Callable[[str], bool] = lambda rest: True   # may it run while the agent works?
    aliases: tuple[str, ...] = ()


def _model(s: Session, rest: str) -> Reply:
    if not rest:
        return Reply(s.describe())
    s.switch(rest)
    return Reply(s.describe(), effect="model")


def _new(s: Session, rest: str) -> Reply:
    log = s.new()
    return Reply(f"new session {log.id}", effect="new")


COMMANDS = [
    Command("model", "[provider:]<id>", "switch model mid-conversation, even across providers "
            "(anthropic:claude-sonnet-5-5, openrouter:z-ai/glm-5.3-flash)", _model,
            busy_ok=lambda rest: not rest),
    Command("models", "[provider] [text]", "models in the catalog, cheapest first",
            lambda s, rest: Reply("\n".join(listing(rest, s.ref.provider)), "block")),
    Command("think", "", "show or hide the model's reasoning",
            lambda s, rest: Reply(effect="think")),
    Command("notes", "", "the model's notes: decisions, rejected approaches, progress",
            lambda s, rest: Reply(fmt_notes(s.agent.context_manager.notes()), "block")),
    Command("cost", "", "tokens and cost for this session",
            lambda s, rest: Reply(fmt_usage(s.agent.total))),
    Command("new", "", "start a new session", _new, busy_ok=lambda rest: False),
    Command("exit", "", "quit", lambda s, rest: Reply(effect="exit"), aliases=("quit",)),
    Command("help", "", "this list", lambda s, rest: Reply(help_text(), "block")),
]
_BY_NAME = {n: c for c in COMMANDS for n in (c.name, *c.aliases)}


def run(session: Session, text: str, busy: bool = False) -> Reply:
    """Run `/name args`. `busy`: the agent is working, so commands that change the
    conversation are refused."""
    name, _, rest = text.removeprefix("/").partition(" ")
    rest = rest.strip()
    cmd = _BY_NAME.get(name)
    if cmd is None:
        return Reply(f"unknown command /{name} (/help)", "error")
    if busy and not cmd.busy_ok(rest):
        return Reply("Stop the agent first.", "error")
    try:
        return cmd.run(session, rest)
    except TarjumanError as e:
        return Reply(str(e), "error")


def help_text() -> str:
    rows = [(f"/{c.name} {c.args}".rstrip(), c.help) for c in COMMANDS]
    width = max(len(left) for left, _ in rows)
    return "\n".join(f"{left:<{width}}  {right}" for left, right in rows)

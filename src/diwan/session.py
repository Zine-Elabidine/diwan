"""One conversation as a front-end drives it: the log, the agent working on it, the model in
use, and what the user has approved for good. Both front-ends hold one; the slash commands
(commands.py) act on it, so a front-end only shows things."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tarjuman import ToolCall

from . import __version__
from .agent import Agent
from .log import Log
from .models import Ref, Router, describe, switch


class Approvals:
    """Which tool calls run without asking. "Always" covers one tool inside the project only:
    a call that reaches outside it asks every time (unless everything is approved, -y)."""

    def __init__(self, auto: bool = False):
        self.auto = auto
        self.always: set[str] = set()

    def covers(self, call: ToolCall, outside: bool) -> bool:
        return self.auto or (call.name in self.always and not outside)

    def answer(self, call: ToolCall, answer: str, outside: bool) -> bool:
        """Record the user's answer ("yes", "no", "always"); True if the call may run."""
        if answer == "always" and not outside:
            self.always.add(call.name)
        return answer in ("yes", "always")


class Session:
    def __init__(self, make_agent: Callable[[Log, Ref], Agent], log: Log, ref: Ref,
                 router: Router, cwd: Path, approvals: Approvals | None = None):
        """make_agent builds an agent on a log for a model, already wired to the front-end
        (its `on` and `approve`); it is called again on /new."""
        self.make_agent, self.router, self.cwd = make_agent, router, cwd
        self.ref = ref
        self.approvals = approvals or Approvals()
        self.log = log
        self.agent = make_agent(log, ref)

    def switch(self, text: str) -> Ref:
        """Continue on another model (`[provider:]model`). Raises TarjumanError and changes
        nothing if the provider is unknown, its key is missing, or the history doesn't fit."""
        self.ref = switch(self.agent, self.router, text)
        return self.ref

    def new(self) -> Log:
        """Start a new conversation on the same model; approvals carry over."""
        self.log = Log.new(cwd=str(self.cwd), provider=self.ref.provider, model=self.ref.model,
                           diwan=__version__)
        self.agent = self.make_agent(self.log, self.ref)
        return self.log

    def describe(self) -> str:
        return describe(self.ref)

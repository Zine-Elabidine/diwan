"""Turns run one at a time, and a message can reach a session whenever it comes: while a turn
runs, it waits in the inbox and the model gets it at its next request; while the session is
idle, it starts a turn (the session wakes up). What arrives just as a turn ends starts the
next one, so nothing is left waiting with no turn to read it.

Front-ends that are not a person typing use it: server mode now, the War Room's messages
between sessions later."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .agent import Agent
from .events import TurnEnded


class Busy(Exception):
    """A turn is already running."""


class Runner:
    def __init__(self, agent: Agent, *, on_wake: Callable[[str], None] = lambda text: None,
                 on_woken_end: Callable[[TurnEnded], None] = lambda ended: None):
        """on_wake: a turn starts on its own, with this text (show it: no one typed it).
        on_woken_end: such a turn ended (no one is waiting for its answer)."""
        self.agent = agent
        self.on_wake, self.on_woken_end = on_wake, on_woken_end
        self._lock = threading.Lock()
        self._busy = False
        self._held: list[str] = []   # messages left when a turn was stopped: they lead the next
        self.thread: threading.Thread | None = None   # the last woken turn's (tests join it)

    @property
    def busy(self) -> bool:
        return self._busy

    def run(self, text: str) -> TurnEnded:
        """A turn in this thread (a prompt someone waits on). What comes in as it ends goes on
        in the background. Raises Busy if a turn is running."""
        with self._lock:
            if self._busy:
                raise Busy
            self._busy = True
            text = self._lead(text)
        ended = self.agent.turn(text)
        self._finish(ended)
        return ended

    def deliver(self, text: str) -> bool:
        """Give the session a message: queued if a turn runs (False), else a turn starts (True)."""
        with self._lock:
            if self._busy:
                self.agent.send(text)
                return False
            self._busy = True
            self._wake(self._lead(text))
            return True

    def _lead(self, text: str) -> str:
        held, self._held = self._held, []
        return "\n\n".join([*held, text])

    def _wake(self, text: str) -> None:
        self.thread = threading.Thread(target=self._woken, args=(text,), daemon=True)
        self.thread.start()

    def _woken(self, text: str) -> None:
        self.on_wake(text)
        ended = self.agent.turn(text)
        self.on_woken_end(ended)
        self._finish(ended)

    def _finish(self, ended: TurnEnded) -> None:
        """Under the same lock as deliver(): a message either made it into the inbox before
        this check, and starts the next turn, or comes after it, and starts one itself."""
        with self._lock:
            left = self.agent.take_inbox()
            if left and ended.reason == "interrupted":
                self._held += left    # stopped on purpose: they wait for the next turn
                left = []
            if left:
                self._wake("\n\n".join(left))
            else:
                self._busy = False

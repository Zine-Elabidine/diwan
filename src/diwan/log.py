"""The register: an append-only, tree-shaped event log, one JSONL file per session.

Every event has an id and a parent. The conversation the model sees is the path from the
current head back to the root; nothing is ever deleted, so branching later is just moving
the head to an older event."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from tarjuman import Message


def sessions_dir() -> Path:
    return Path(os.environ.get("DIWAN_HOME", Path.home() / ".diwan")) / "sessions"


class Kind(StrEnum):
    """What an event records. Stored as its plain string, so old session files still load."""
    SESSION = "session"            # the first event: id, cwd, provider, model
    MESSAGE = "message"            # a conversation message (tarjuman's neutral format)
    MASK = "mask"                  # tool outputs cleared from later requests (clearing.py)
    SUMMARY = "summary"            # the oldest messages, replaced in later requests by a summary
    MODEL_SWITCH = "model_switch"  # the conversation moved to another provider/model
    MODEL = "model"                # the same, from older sessions (no provider)
    APPROVAL = "approval"          # the user's answer to a tool call
    ERROR = "error"                # a failed request or an internal error; never sent
    TURN_END = "turn_end"          # why a turn stopped, its steps and usage
    REWIND = "rewind"              # the user went back: later events continue from its parent


@dataclass
class Event:
    id: str
    parent: str | None
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Summary:
    """A "summary" event: the first `cut` messages of the branch are sent as `text` instead.
    `point` is how many messages the branch had when the summary was made. `model_text` is
    the model's part of `text`, the part the next summary updates."""
    cut: int
    text: str
    point: int
    model_text: str


@dataclass
class _Branch:
    """What the path to `head` adds up to, kept so each request doesn't re-walk the log."""
    head: str | None = None
    messages: list[Message] = field(default_factory=list)
    masked: dict[str, str] = field(default_factory=dict)
    mask_points: list[int] = field(default_factory=list)
    summary: Summary | None = None   # the latest one: each summary covers the ones before it

    def add(self, e: Event) -> None:
        if e.type == Kind.MESSAGE:
            self.messages.append(Message.from_dict(e.data))
        elif e.type == Kind.MASK:
            self.masked.update(e.data["entries"])
            self.mask_points.append(len(self.messages))
        elif e.type == Kind.SUMMARY:
            self.summary = Summary(e.data["cut"], e.data["text"], len(self.messages),
                                   e.data.get("model_text", e.data["text"]))
        self.head = e.id


class Log:
    def __init__(self, path: Path, events: list[Event]):
        self.path = path
        self.events = events
        self.by_id = {e.id: e for e in events}
        self.head: str | None = events[-1].id if events else None
        self._branch_cache: _Branch | None = None

    @classmethod
    def new(cls, **meta: Any) -> Log:
        d = sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        log = cls(d / f"{sid}.jsonl", [])
        log.append(Kind.SESSION, {"id": sid, **meta})
        return log

    @classmethod
    def load(cls, path: Path) -> Log:
        events = [Event(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        return cls(path, events)

    @classmethod
    def latest(cls, cwd: str) -> Log | None:
        """The most recent session started in `cwd` by the user (a child agent's isn't one)."""
        d = sessions_dir()
        if not d.exists():
            return None
        for p in sorted(d.glob("*.jsonl"), reverse=True):
            with p.open(encoding="utf-8") as f:
                first = f.readline()
            meta = json.loads(first)["data"] if first else {}
            if meta.get("cwd") == cwd and "parent" not in meta:
                return cls.load(p)
        return None

    @property
    def id(self) -> str:
        return self.events[0].data["id"]

    def append(self, type: Kind, data: dict[str, Any]) -> str:
        e = Event(uuid.uuid4().hex[:12], self.head, type, data)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(e.__dict__, ensure_ascii=False) + "\n")
        self.events.append(e)
        self.by_id[e.id] = e
        if self._branch_cache and self._branch_cache.head == self.head:
            self._branch_cache.add(e)   # the branch grew by one event: no re-walk
        self.head = e.id
        return e.id

    def add_message(self, m: Message) -> str:
        return self.append(Kind.MESSAGE, m.to_dict())

    def path_to_head(self) -> list[Event]:
        out, cur = [], self.head
        while cur is not None:
            e = self.by_id[cur]
            out.append(e)
            cur = e.parent
        return out[::-1]

    def _branch(self) -> _Branch:
        b = self._branch_cache
        if b is None or b.head != self.head:   # first use, or the head moved to another branch
            b = _Branch()
            for e in self.path_to_head():
                b.add(e)
            self._branch_cache = b
        return b

    # Callers get their own lists, never the cache's. The Message objects are shared: treat
    # them as read-only (copy one to change it, as context.apply does).

    def messages(self) -> list[Message]:
        """The conversation along the current branch."""
        return list(self._branch().messages)

    def masked(self) -> dict[str, str]:
        """What earlier "mask" events on this branch cleared (see clearing.py)."""
        return dict(self._branch().masked)

    def mask_points(self) -> list[int]:
        """For each "mask" event on this branch, how many messages came before it."""
        return list(self._branch().mask_points)

    def summary(self) -> Summary | None:
        """The latest "summary" event on this branch, if any."""
        return self._branch().summary

    def rewind(self, n: int = 1) -> str | None:
        """Go back to just before the user's n-th last message, and return that message's text
        (to edit and send again), or None if there aren't that many. Nothing is removed: a
        "rewind" event starts a new branch there, and the old branch stays in the log."""
        typed = [e for e in self.path_to_head()
                 if e.type == Kind.MESSAGE and e.data.get("role") == "user"]
        if n < 1 or n > len(typed):
            return None
        target = typed[-n]
        old = self.head
        self.head = target.parent
        self.append(Kind.REWIND, {"from": old, "to": target.parent})
        return Message.from_dict(target.data).text

    def current_model(self) -> tuple[str | None, str | None]:
        """(provider, model) in use at the head: the last switch on this branch, else the
        session's start. Older sessions logged switches as "model" events, without a provider."""
        start = self.events[0].data if self.events else {}
        provider, model = start.get("provider"), start.get("model")
        for e in self.path_to_head():
            if e.type == Kind.MODEL_SWITCH:
                provider, model = e.data.get("provider"), e.data.get("model")
            elif e.type == Kind.MODEL:
                model = e.data.get("model")
        return provider, model

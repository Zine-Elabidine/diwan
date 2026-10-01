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
from pathlib import Path
from typing import Any

from tarjuman import Message


def sessions_dir() -> Path:
    return Path(os.environ.get("DIWAN_HOME", Path.home() / ".diwan")) / "sessions"


@dataclass
class Event:
    id: str
    parent: str | None
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)


@dataclass
class _Branch:
    """What the path to `head` adds up to, kept so each request doesn't re-walk the log."""
    head: str | None = None
    messages: list[Message] = field(default_factory=list)
    masked: dict[str, str] = field(default_factory=dict)
    mask_points: list[int] = field(default_factory=list)

    def add(self, e: Event) -> None:
        if e.type == "message":
            self.messages.append(Message.from_dict(e.data))
        elif e.type == "mask":
            self.masked.update(e.data["entries"])
            self.mask_points.append(len(self.messages))
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
        log.append("session", {"id": sid, **meta})
        return log

    @classmethod
    def load(cls, path: Path) -> Log:
        events = [Event(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        return cls(path, events)

    @classmethod
    def latest(cls, cwd: str) -> Log | None:
        """The most recent session started in `cwd`."""
        d = sessions_dir()
        if not d.exists():
            return None
        for p in sorted(d.glob("*.jsonl"), reverse=True):
            with p.open(encoding="utf-8") as f:
                first = f.readline()
            if first and json.loads(first)["data"].get("cwd") == cwd:
                return cls.load(p)
        return None

    @property
    def id(self) -> str:
        return self.events[0].data["id"]

    def append(self, type: str, data: dict[str, Any]) -> str:
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
        return self.append("message", m.to_dict())

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

    def current_model(self) -> tuple[str | None, str | None]:
        """(provider, model) in use at the head: the last switch on this branch, else the
        session's start. Older sessions logged switches as "model" events, without a provider."""
        start = self.events[0].data if self.events else {}
        provider, model = start.get("provider"), start.get("model")
        for e in self.path_to_head():
            if e.type == "model_switch":
                provider, model = e.data.get("provider"), e.data.get("model")
            elif e.type == "model":
                model = e.data.get("model")
        return provider, model

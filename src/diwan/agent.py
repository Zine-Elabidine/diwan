"""The loop. A turn is everything done for one user message; a step is one model request
plus the tools it asked for. Every step, tool result, retry and ending is logged."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from tarjuman import (BlockStart, Event, Finish, Message, ReasoningDelta, TarjumanError, Text,
                      TextDelta, Tool, ToolCall, ToolCallDelta, ToolResult, Usage)
from tarjuman.types import Reasoning

from .log import Log
from .tools import Spec, ToolError

State = Literal["thinking", "running", "waiting", "idle"]
Reason = Literal["done", "max_tokens", "max_steps", "interrupted", "error"]


class Provider(Protocol):
    provider: str

    def stream(self, model: str, messages: list[Message], *, tools: list[Tool] | None = ...,
               max_tokens: int | None = ...) -> Any: ...


# --- what the loop tells the UI ---------------------------------------------------------------

@dataclass
class StateChanged:
    state: State


@dataclass
class ToolStarted:
    call: ToolCall


@dataclass
class ToolFinished:
    call: ToolCall
    result: ToolResult


@dataclass
class Retrying:
    error: TarjumanError
    attempt: int
    wait: float


@dataclass
class TurnEnded:
    reason: Reason
    steps: int
    usage: Usage
    error: str | None = None


UIEvent = StateChanged | ToolStarted | ToolFinished | Retrying | TurnEnded | Event


@dataclass
class Limits:
    max_steps: int = 60
    max_tokens: int = 16_000
    max_retries: int = 4


class Agent:
    def __init__(self, provider: Provider, model: str, log: Log, tools: dict[str, Spec],
                 system: str, *, approve: Callable[[ToolCall, Spec], bool] = lambda c, s: True,
                 on: Callable[[UIEvent], None] = lambda e: None, limits: Limits | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.provider, self.model, self.log, self.tools = provider, model, log, tools
        self.system, self.approve, self.on = system, approve, on
        self.limits = limits or Limits()
        self.sleep = sleep
        self.total = Usage()

    def turn(self, text: str) -> TurnEnded:
        self.log.add_message(Message.user(text))
        steps, usage = 0, Usage()
        try:
            while True:
                if steps >= self.limits.max_steps:
                    return self._end("max_steps", steps, usage)
                steps += 1
                msg = self._sample()
                usage += msg.usage or Usage()
                self.log.add_message(msg)
                if msg.stop == "max_tokens":
                    return self._end("max_tokens", steps, usage)
                if not msg.tool_calls:
                    return self._end("done", steps, usage)
                self._run_tools(msg.tool_calls)
        except KeyboardInterrupt:
            return self._end("interrupted", steps, usage)
        except TarjumanError as e:
            return self._end("error", steps, usage, str(e))

    # --- steps ---------------------------------------------------------------------------------

    def _sample(self) -> Message:
        messages = [Message.system(self.system), *self.log.messages()]
        tools = [s.tool for s in self.tools.values()]
        attempt = 0
        while True:
            self.on(StateChanged("thinking"))
            partial: list[Any] = []
            try:
                for ev in self.provider.stream(self.model, messages, tools=tools,
                                               max_tokens=self.limits.max_tokens):
                    self.on(ev)
                    _collect(partial, ev)
                    if isinstance(ev, Finish):
                        return ev.message
                raise TarjumanError("SERVER_ERROR", "stream ended without a finish")
            except KeyboardInterrupt:
                # keep what was streamed, flagged, so nothing is lost and the model sees where it stopped
                kept = [b for b in partial if not isinstance(b, ToolCall)]
                if kept:
                    self.log.add_message(Message("assistant", kept, self.provider.provider,
                                                 self.model, None, "interrupted"))
                raise
            except TarjumanError as e:
                attempt += 1
                # failed attempts are logged but never become part of the conversation
                self.log.append("error", {"code": e.code, "message": e.message, "attempt": attempt})
                if not e.retryable or attempt > self.limits.max_retries:
                    raise
                wait = e.retry_after or min(2 ** attempt, 30)
                self.on(Retrying(e, attempt, wait))
                self.sleep(wait)

    def _run_tools(self, calls: list[ToolCall]) -> None:
        results: list[ToolResult] = []
        try:
            for call in calls:
                results.append(self._run_one(call))
        finally:
            # results already produced are always saved, even on interrupt;
            # unanswered calls get a synthetic error when the history is sent
            if results:
                self.log.add_message(Message("tool", results))

    def _run_one(self, call: ToolCall) -> ToolResult:
        spec = self.tools.get(call.name)
        if spec is None:
            return ToolResult(call.id, f"Unknown tool `{call.name}`. Available: "
                                       f"{', '.join(self.tools)}", True)
        try:
            args = call.args()
        except ValueError as e:
            return ToolResult(call.id, f"Invalid JSON arguments: {e}", True)
        if not spec.readonly:
            self.on(StateChanged("waiting"))
            allowed = self.approve(call, spec)
            self.log.append("approval", {"call_id": call.id, "allowed": allowed})
            if not allowed:
                result = ToolResult(call.id, "The user denied this action. Ask them how to "
                                             "proceed instead of retrying.", True)
                self.on(ToolFinished(call, result))
                return result
        self.on(StateChanged("running"))
        self.on(ToolStarted(call))
        try:
            result = ToolResult(call.id, spec.run(**args))
        except ToolError as e:
            result = ToolResult(call.id, str(e), True)
        except TypeError as e:
            result = ToolResult(call.id, f"Bad arguments for {call.name}: {e}", True)
        except Exception as e:  # a tool crash must not kill the session
            result = ToolResult(call.id, f"{type(e).__name__}: {e}", True)
        self.on(ToolFinished(call, result))
        return result

    def _end(self, reason: Reason, steps: int, usage: Usage, error: str | None = None) -> TurnEnded:
        self.total += usage
        self.log.append("turn_end", {"reason": reason, "steps": steps, "usage": usage.__dict__,
                                     **({"error": error} if error else {})})
        ended = TurnEnded(reason, steps, usage, error)
        self.on(StateChanged("idle"))
        self.on(ended)
        return ended


def _collect(blocks: list[Any], ev: Event) -> None:
    """Rebuild the partial message from stream events (used only if interrupted)."""
    if isinstance(ev, BlockStart):
        blocks.append(ToolCall(ev.id or "", ev.name or "", "") if ev.kind == "tool_call"
                      else Text("") if ev.kind == "text" else Reasoning(""))
    elif isinstance(ev, TextDelta | ReasoningDelta) and ev.index < len(blocks):
        blocks[ev.index].text += ev.text
    elif isinstance(ev, ToolCallDelta) and ev.index < len(blocks):
        blocks[ev.index].arguments += ev.arguments

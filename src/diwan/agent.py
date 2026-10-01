"""The loop. A turn is everything done for one user message; a step is one model request
plus the tools it asked for. Every step, tool result, retry and ending is logged."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from tarjuman import (BlockEnd, BlockStart, Cancel, Event, Finish, Message, Reasoning,
                      ReasoningDelta, Replay, TarjumanError, Text, TextDelta, Tool, ToolCall,
                      ToolCallDelta, ToolResult, Unknown, Usage)
from tarjuman import errors
from tarjuman import limits as tokens

from . import context as ctx
from .log import Log
from .tools import Spec, ToolError

State = Literal["thinking", "running", "waiting", "idle"]
Reason = Literal["done", "max_tokens", "max_steps", "interrupted", "error"]

# another model's chars-per-token, reused for this one, assumes 15% more tokens (tokenizers differ;
# measured: DeepSeek 3.08, Claude 2.68 on the same session)
OTHER_TOKENIZER = 0.85

INTERRUPTED = ("The user interrupted the previous turn on purpose. If a tool call was cut off, "
               "it may have partly run: check before repeating it.")


class Provider(Protocol):
    provider: str

    def stream(self, model: str, messages: list[Message], *, tools: list[Tool] | None = ...,
               max_tokens: int | None = ..., cancel: Cancel | None = ...) -> Any: ...


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
class ContextUse:
    """How full the context is, in the current model's own tokens."""
    used: int             # tokens the next request will hold
    window: int | None    # the model's context window, if known
    usable: int | None    # the window minus the room kept for the answer
    exact: bool           # True right after a reply from this model; otherwise partly estimated

    @property
    def fraction(self) -> float | None:
        return self.used / self.usable if self.usable else None


@dataclass
class ContextChanged:
    context: ContextUse


@dataclass
class ContextCleared:
    """Old tool outputs were cleared to save context (the log keeps them)."""
    text: str


@dataclass
class TurnEnded:
    reason: Reason
    steps: int
    usage: Usage
    error: str | None = None


UIEvent = (StateChanged | ToolStarted | ToolFinished | Retrying | TurnEnded | ContextChanged
           | ContextCleared | Event)


@dataclass
class Limits:
    max_steps: int = 60
    max_tokens: int = 16_000
    max_retries: int = 4
    context: int | None = None   # cap on the context window (also used when it is unknown)


class Agent:
    def __init__(self, provider: Provider, model: str, log: Log, tools: dict[str, Spec],
                 system: str, *, approve: Callable[[ToolCall, Spec], bool] = lambda c, s: True,
                 on: Callable[[UIEvent], None] = lambda e: None, limits: Limits | None = None,
                 sleep: Callable[[float], None] | None = None):
        self.provider, self.model, self.log, self.tools = provider, model, log, tools
        self.system, self.approve, self.on = system, approve, on
        self.limits = limits or Limits()
        self.sleep = sleep  # for tests; by default retry waits end early on interrupt
        self.total = Usage()
        self._cancel = Cancel()
        self._ratio = tokens.CHARS_PER_TOKEN   # the current model's chars per token, from context()
        self.context_use = self.context()

    def use(self, provider: Provider, model: str, system: str) -> None:
        """Continue the same conversation on another model, maybe through another provider.
        Tarjuman adapts the history on the next request (docs/format.md in tarjuman). Refuses
        (TarjumanError) when the history doesn't fit the new model's window."""
        if (provider.provider, model) == (self.provider.provider, self.model):
            return
        fit = self.context(provider, model)
        if fit.usable and fit.used > fit.usable:
            raise TarjumanError(
                errors.CONTEXT_WINDOW_EXCEEDED,
                f"this conversation is about {fit.used:,} tokens for {model}, more than the "
                f"{fit.usable:,} it can take ({fit.window:,} minus room for the answer). Pick a "
                "model with a bigger window, or start a new session with /new.")
        self.provider, self.model, self.system = provider, model, system
        self.log.append("model_switch", {"provider": provider.provider, "model": model})
        self.log.add_message(Message.system(
            f"The conversation now continues on `{model}` (via {provider.provider}). Earlier "
            "replies may have been written by other models."))
        self._context_changed()

    def context(self, provider: Provider | None = None, model: str | None = None) -> ContextUse:
        """How many tokens the next request holds, counted for `model` (default: the current
        one). Exact from this model's last usage report, plus an estimate for what came after it,
        at a chars-per-token ratio calibrated on that report. With no report from this model
        (a new session, or right after /model) the whole history is estimated."""
        provider, model = provider or self.provider, model or self.model
        request = [Message.system(self.system), *self._view()]
        tools = [s.tool for s in self.tools.values()]
        used, exact, borrowed = None, False, None
        for i in range(len(request) - 1, 0, -1):
            m = request[i]
            prompt = (m.usage.input + m.usage.cache_read + m.usage.cache_write) if m.usage else 0
            if m.role != "assistant" or not prompt:
                continue
            if m.model != model or m.provider != provider.provider:
                if borrowed is None:   # the most recent other model's ratio, as a fallback
                    borrowed = tokens.ratio(request[:i], tools, prompt) or 0.0
                continue
            ratio = tokens.ratio(request[:i], tools, prompt) or tokens.CHARS_PER_TOKEN
            if any(point >= i for point in self.log.mask_points()):
                # outputs were cleared after this report: the reported count is too high now
                used = tokens.estimate(request, tools, ratio)
                break
            after = request[i + 1:]
            used = prompt + m.usage.output + (tokens.estimate(after, None, ratio) if after else 0)
            exact = not after
            break
        if used is None:
            # no report from this model: the same text measured on another model, plus a margin
            # for a different tokenizer; else the default
            ratio = min(borrowed * OTHER_TOKENIZER, tokens.CHARS_PER_TOKEN) if borrowed \
                else tokens.CHARS_PER_TOKEN
            used = tokens.estimate(request, tools, ratio)
        window = _ask(provider, "context_window", model)
        if self.limits.context:
            window = min(window, self.limits.context) if window else self.limits.context
        info = _ask(provider, "info", model)
        reserve = min(self.limits.max_tokens, info.max_output) if info and info.max_output \
            else self.limits.max_tokens
        if (provider, model) == (self.provider, self.model):
            self._ratio = ratio
        return ContextUse(used, window, max(window - reserve, 0) if window else None, exact)

    def _context_changed(self) -> None:
        try:
            self.context_use = self.context()
        except Exception as e:  # the gauge is informative: a bug in it must not stop the turn
            self.log.append("error", {"code": "INTERNAL", "message": f"context: {e!r}",
                                      "traceback": traceback.format_exc()})
            return
        self.on(ContextChanged(self.context_use))

    def interrupt(self) -> None:
        """Stop the running turn now (safe from any thread). One signal reaches everything the
        turn is doing: the model stream is closed (the provider stops generating), a running
        command is killed with everything it started, a retry wait ends. Everything produced
        so far is kept."""
        self._cancel.cancel()

    def _check_stop(self) -> None:
        if self._cancel.cancelled:
            raise KeyboardInterrupt

    def turn(self, text: str) -> TurnEnded:
        self._cancel = Cancel()
        self.log.add_message(Message.user(text))
        steps, usage = 0, Usage()
        try:
            while True:
                self._check_stop()
                if steps >= self.limits.max_steps:
                    return self._end("max_steps", steps, usage)
                steps += 1
                msg = self._sample()
                usage += msg.usage or Usage()
                self.log.add_message(msg)
                self._context_changed()
                if msg.stop == "max_tokens":
                    return self._end("max_tokens", steps, usage)
                if not msg.tool_calls:
                    return self._end("done", steps, usage)
                self._run_tools(msg.tool_calls)
        except KeyboardInterrupt:
            # its own event, rendered as a reminder when the history is sent (never an edit)
            self.log.add_message(Message.system(INTERRUPTED))
            return self._end("interrupted", steps, usage)
        except TarjumanError as e:
            return self._end("error", steps, usage, str(e))
        except Exception as e:  # a bug must end the turn, never the session
            self.log.append("error", {"code": "INTERNAL", "message": repr(e),
                                      "traceback": traceback.format_exc()})
            return self._end("error", steps, usage,
                             f"{type(e).__name__}: {e} (an error in Diwan; the session log has details)")

    # --- steps ---------------------------------------------------------------------------------

    def _view(self) -> list[Message]:
        """The conversation as the model sees it: the log's messages, with cleared outputs."""
        return ctx.apply(self.log.messages(), self.log.masked())

    def _maybe_clear(self) -> None:
        """Clear old tool outputs once the context is half full (context.py)."""
        use = self.context_use
        if not use.usable or (use.fraction or 0) < ctx.MASK_AT:
            return
        p = ctx.plan(self.log.messages(), self.log.masked(), use.usable, self._ratio)
        if p is None:
            return
        self.log.append("mask", {"entries": p.entries, "saved": p.saved})
        self.on(ContextCleared(ctx.saved_text(p)))
        self._context_changed()

    def _sample(self) -> Message:
        self._maybe_clear()
        messages = [Message.system(self.system), *self._view()]
        tools = [s.tool for s in self.tools.values()]
        attempt = 0
        while True:
            self.on(StateChanged("thinking"))
            partial: list[Any] = []
            done: dict[int, tuple[Any, Any]] = {}   # finished blocks: index -> (block, replay entry)
            try:
                for ev in self.provider.stream(self.model, messages, tools=tools,
                                               max_tokens=self.limits.max_tokens,
                                               cancel=self._cancel):
                    self.on(ev)
                    _collect(partial, ev)
                    if isinstance(ev, BlockEnd) and ev.index < len(partial):
                        done[ev.index] = (ev.block or partial[ev.index], ev.replay)
                    self._check_stop()
                    if isinstance(ev, Finish):
                        return ev.message
                raise TarjumanError(errors.SERVER_ERROR, "stream ended without a finish")
            except KeyboardInterrupt:
                self._keep_interrupted(partial, done)
                raise
            except TarjumanError as e:
                if e.code == errors.CANCELLED:
                    self._keep_interrupted(partial, done)
                    raise KeyboardInterrupt from None
                attempt += 1
                # failed attempts are logged but never become part of the conversation
                self.log.append("error", {"code": e.code, "message": e.message, "attempt": attempt})
                if not e.retryable or attempt > self.limits.max_retries:
                    raise
                wait = e.retry_after or min(2 ** attempt, 30)
                self.on(Retrying(e, attempt, wait))
                if self.sleep:
                    self.sleep(wait)
                else:
                    self._cancel.wait(wait)
                self._check_stop()

    def _keep_interrupted(self, partial: list[Any], done: dict[int, tuple[Any, Any]]) -> None:
        """Nothing streamed is lost. Finished blocks (signed, complete) go in `content` and can
        be sent back as they are; the block cut off mid-stream goes in `partial`: kept in the
        log, never sent to a model (docs/format.md in tarjuman, decision 4)."""
        order = sorted(done)
        content = [done[i][0] for i in order]
        entries = [done[i][1] for i in order]
        cut = [b for i, b in enumerate(partial) if i not in done
               and not isinstance(b, Unknown)
               and (b.arguments if isinstance(b, ToolCall) else b.text)]
        if content or cut:
            self.log.add_message(Message(
                "assistant", content, self.provider.provider, self.model, None, "interrupted",
                getattr(self.provider, "protocol", None),
                replay=Replay(None, entries) if any(e is not None for e in entries) else None,
                partial=cut or None))

    def _run_tools(self, calls: list[ToolCall]) -> None:
        results: list[ToolResult] = []
        try:
            for call in calls:
                self._check_stop()
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
            extra = {"cancel": self._cancel} if spec.cancellable else {}
            result = ToolResult(call.id, spec.run(**args, **extra))
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
        self._context_changed()
        self.log.append("turn_end", {"reason": reason, "steps": steps, "usage": usage.__dict__,
                                     **({"error": error} if error else {})})
        ended = TurnEnded(reason, steps, usage, error)
        self.on(StateChanged("idle"))
        self.on(ended)
        return ended


def _ask(provider: Any, method: str, model: str) -> Any:
    """Optional knowledge from the provider (window, catalog info); None if it has none."""
    fn = getattr(provider, method, None)
    try:
        return fn(model) if fn else None
    except Exception:  # a lookup must never break the session
        return None


def _collect(blocks: list[Any], ev: Event) -> None:
    """Rebuild the partial message from stream events (used only if interrupted)."""
    if isinstance(ev, BlockStart):
        blocks.append(ToolCall(ev.id or "", ev.name or "", "") if ev.kind == "tool_call"
                      else Text("") if ev.kind == "text"
                      else Reasoning("") if ev.kind == "reasoning" else Unknown(ev.kind, {}))
    elif isinstance(ev, TextDelta | ReasoningDelta) and ev.index < len(blocks):
        blocks[ev.index].text += ev.text
    elif isinstance(ev, ToolCallDelta) and ev.index < len(blocks):
        blocks[ev.index].arguments += ev.arguments

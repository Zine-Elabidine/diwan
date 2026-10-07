"""The loop. A turn is everything done for one user message; a step is one model request
plus the tools it asked for. Every step, tool result, retry and ending is logged."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from tarjuman import (BlockEnd, BlockStart, Cancel, Event, Finish, Message, Provider, Reasoning,
                      ReasoningDelta, Replay, TarjumanError, Text, TextDelta, ToolCall,
                      ToolCallDelta, ToolResult, Unknown, Usage, errors)

from . import clearing, summary
from .context import ContextManager, ContextUse
from .events import (ContextChanged, ContextCleared, ContextSummarized, Reason, Retrying,
                     StateChanged, ToolFinished, ToolStarted, TurnEnded, UIEvent)
from .log import Kind, Log
from .paths import Access, PathPolicy
from .tools import Tool, ToolContext, ToolError, access

class Interrupted(BaseException):
    """The user stopped the turn (Agent.interrupt). A BaseException, like KeyboardInterrupt, so
    no `except Exception` on the way (a tool, a provider, a UI callback) can swallow it."""


# what stops a turn: our own signal, or a real Ctrl+C in plain mode
STOPS = (Interrupted, KeyboardInterrupt)

INTERRUPTED = ("The user interrupted the previous turn on purpose. If a tool call was cut off, "
               "it may have partly run: check before repeating it.")


@dataclass
class Limits:
    max_steps: int = 60
    max_tokens: int = 16_000
    max_retries: int = 4
    context: int | None = None   # cap on the context window (also used when it is unknown)
    summaries: bool = True       # summarize the oldest messages automatically when the context fills
    in_place: bool = True        # summarize by resending the conversation as is (summary.IN_PLACE);
                                 # False: a plain-text transcript, also the fallback


class Agent:
    def __init__(self, provider: Provider, model: str, log: Log, tools: dict[str, Tool],
                 system: str | Callable[[str, str], str], *,
                 approve: Callable[[ToolCall, Tool, bool], bool] = lambda c, s, outside: True,
                 on: Callable[[UIEvent], None] = lambda e: None, limits: Limits | None = None,
                 sleep: Callable[[float], None] | None = None, paths: PathPolicy | None = None):
        self.provider, self.model, self.log, self.tools = provider, model, log, tools
        # the system prompt names the model, so it is built again for each one
        self._prompt: Callable[[str, str], str] = (
            system if callable(system) else _fixed(system))
        self.system = self._prompt(provider.provider, model)
        self.approve, self.on = approve, on
        self.limits = limits or Limits()
        self.sleep = sleep  # for tests; by default retry waits end early on interrupt
        self.paths = paths or PathPolicy(Path(log.events[0].data.get("cwd") or "."))
        self.total = Usage()
        self._cancel = Cancel()
        self.context_manager = ContextManager(log, self.limits.max_tokens, self.limits.context)
        self.context_use = self.context()

    def use(self, provider: Provider, model: str) -> None:
        """Continue the same conversation on another model, maybe through another provider.
        Tarjuman adapts the history on the next request (docs/format.md in tarjuman). Refuses
        (TarjumanError) when the history doesn't fit the new model's window."""
        if (provider.provider, model) == (self.provider.provider, self.model):
            return
        fit = self.context(provider, model)
        if fit.overflows:
            raise TarjumanError(
                errors.CONTEXT_WINDOW_EXCEEDED,
                f"this conversation is about {fit.used:,} tokens for {model}, more than the "
                f"{fit.usable:,} it can take ({fit.window:,} minus room for the answer). Pick a "
                "model with a bigger window, or start a new session with /new.")
        self.provider, self.model = provider, model
        self.system = self._prompt(provider.provider, model)
        self.log.append(Kind.MODEL_SWITCH, {"provider": provider.provider, "model": model})
        self.log.add_message(Message.system(
            f"The conversation now continues on `{model}` (via {provider.provider}). Earlier "
            "replies may have been written by other models."))
        self._context_changed()

    def context(self, provider: Provider | None = None, model: str | None = None) -> ContextUse:
        """How full the next request is, counted for `model` (default: the current one) with
        that model's own system prompt. See ContextManager.measure."""
        provider, model = provider or self.provider, model or self.model
        current = (provider.provider, model) == (self.provider.provider, self.model)
        system = self.system if current else self._prompt(provider.provider, model)
        tools = [t.definition for t in self.tools.values()]
        return self.context_manager.measure(system, tools, provider, model)

    def _context_changed(self) -> None:
        try:
            self.context_use = self.context()
        except Exception as e:  # the gauge is informative: a bug in it must not stop the turn
            self.log.append(Kind.ERROR, {"code": "INTERNAL", "message": f"context: {e!r}",
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
            raise Interrupted

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
        except STOPS:
            # its own event, rendered as a reminder when the history is sent (never an edit)
            self.log.add_message(Message.system(INTERRUPTED))
            return self._end("interrupted", steps, usage)
        except TarjumanError as e:
            return self._end("error", steps, usage, str(e))
        except Exception as e:  # a bug must end the turn, never the session
            self.log.append(Kind.ERROR, {"code": "INTERNAL", "message": repr(e),
                                      "traceback": traceback.format_exc()})
            return self._end("error", steps, usage,
                             f"{type(e).__name__}: {e} (an error in Diwan; the session log has details)")

    # --- steps ---------------------------------------------------------------------------------

    def _maybe_clear(self) -> None:
        p = self.context_manager.clear(self.context_use)
        if p is not None:
            self.on(ContextCleared(clearing.saved_text(p)))
            self._context_changed()

    def _maybe_summarize(self) -> None:
        """After clearing (which is free): if the context is still past the trigger, summarize."""
        use = self.context_use
        if self.limits.summaries and use.usable and summary.due(use.used, use.usable):
            self._summarize()

    def _summarize(self) -> bool:
        """One summary, shown as a notice. A failed summary is logged and the turn goes on
        unsummarized; an interrupt stops the turn as usual. True when a summary was logged."""
        before = self.context_use.used
        try:
            in_place = (self.system, [t.definition for t in self.tools.values()]) \
                if self.limits.in_place else None
            r = self.context_manager.summarize(self.context_use, self.provider, self.model,
                                               self._cancel, in_place)
        except TarjumanError as e:
            if e.code == errors.CANCELLED:
                raise Interrupted from None
            self.log.append(Kind.ERROR, {"code": e.code, "message": f"summary: {e.message}"})
            self.on(ContextSummarized(f"could not summarize ({e.code}); going on without"))
            return False
        if r is None:
            self.on(ContextSummarized("nothing to summarize yet"))
            return False
        self.total += r.usage
        self._context_changed()
        self.on(ContextSummarized(
            f"summarized {r.replaced} message{'s' if r.replaced != 1 else ''} "
            f"(~{before:,} → ~{self.context_use.used:,} tokens)"))
        return True

    def compact(self) -> None:
        """Summarize now (/compact), between turns."""
        self._cancel = Cancel()
        self._context_changed()
        try:
            self._summarize()
        except STOPS:
            self.on(ContextSummarized("summary stopped"))

    def _sample(self) -> Message:
        self._maybe_clear()
        self._maybe_summarize()
        messages = [Message.system(self.system), *self.context_manager.view()]
        tools = [t.definition for t in self.tools.values()]
        attempt, squeezed = 0, False
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
            except STOPS:
                self._keep_interrupted(partial, done)
                raise
            except TarjumanError as e:
                if e.code == errors.CANCELLED:
                    self._keep_interrupted(partial, done)
                    raise Interrupted from None
                attempt += 1
                # failed attempts are logged but never become part of the conversation
                self.log.append(Kind.ERROR, {"code": e.code, "message": e.message, "attempt": attempt})
                if (e.code == errors.CONTEXT_WINDOW_EXCEEDED and not squeezed
                        and self.limits.summaries):
                    # the gauge was wrong or the window smaller than known: summarize, retry once
                    squeezed = True
                    if self._summarize():
                        messages = [Message.system(self.system), *self.context_manager.view()]
                        continue
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
                self.log.add_message(Message("tool", [*results]))

    def _run_one(self, call: ToolCall) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"Unknown tool `{call.name}`. Available: "
                                       f"{', '.join(self.tools)}", True)
        try:
            args = call.args()
        except ValueError as e:
            return ToolResult(call.id, f"Invalid JSON arguments: {e}", True)
        needed = access(tool, args, self.paths)
        if needed is Access.DENIED:
            result = ToolResult(call.id, self.paths.why(str(args.get(tool.path_arg or ""))), True)
            self.on(ToolFinished(call, result))
            return result
        if not tool.readonly or needed is Access.ASK:
            self.on(StateChanged("waiting"))
            # `outside`: the call reaches beyond the project; "always" approvals don't cover it
            allowed = self.approve(call, tool, needed is Access.ASK)
            self.log.append(Kind.APPROVAL, {"call_id": call.id, "allowed": allowed})
            if not allowed:
                result = ToolResult(call.id, "The user denied this action. Ask them how to "
                                             "proceed instead of retrying.", True)
                self.on(ToolFinished(call, result))
                return result
        self.on(StateChanged("running"))
        self.on(ToolStarted(call))
        try:
            ctx = ToolContext(self.paths, self._cancel, self.log)
            result = ToolResult(call.id, tool.run(ctx, **args))
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
        self.log.append(Kind.TURN_END, {"reason": reason, "steps": steps, "usage": usage.__dict__,
                                     **({"error": error} if error else {})})
        ended = TurnEnded(reason, steps, usage, error)
        self.on(StateChanged("idle"))
        self.on(ended)
        return ended


def _fixed(system: str) -> Callable[[str, str], str]:
    return lambda provider, model: system


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

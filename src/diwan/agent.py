"""The loop. A turn is everything done for one user message; a step is one model request
plus the tools it asked for. Every step, tool result, retry and ending is logged."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from tarjuman import (BlockEnd, BlockStart, Cancel, Event, Finish, Message, Provider, Reasoning,
                      ReasoningDelta, Replay, TarjumanError, Text, TextDelta, ToolCall,
                      ToolCallDelta, ToolResult, Unknown, Usage, errors)

from . import clearing, prompt, summary
from .context import ContextManager, ContextUse
from .events import (ChildEvent, ContextChanged, ContextCleared, ContextSummarized, Reason, Retrying,
                     StateChanged, ToolFinished, ToolStarted, TurnEnded, UIEvent, UserAdded)
from .log import Kind, Log
from .paths import Access, PathPolicy
from .tools import Tool, ToolContext, ToolError, access

class Interrupted(BaseException):
    """The user stopped the turn (Agent.interrupt). A BaseException, like KeyboardInterrupt, so
    no `except Exception` on the way (a tool, a provider, a UI callback) can swallow it."""


MAX_PARALLEL = 8   # tool calls run at the same time (see _run_tools)

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
                 sleep: Callable[[float], None] | None = None, paths: PathPolicy | None = None,
                 child: bool = False, denied: frozenset[str] = frozenset()):
        """child: started by another agent (it can't start agents itself).
        denied: tools kept in the request (a fork's must match its parent's, for the cache)
        but refused when called."""
        self.provider, self.model, self.log, self.tools = provider, model, log, tools
        self.child, self.denied = child, denied
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
        self._children_usage = Usage()   # what child agents spent during the running turn
        self._lock = threading.Lock()
        self._inbox: list[str] = []   # typed while a turn runs: given to the model at the next step
        self._inbox_lock = threading.Lock()
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
        self.record_model()
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

    def record_model(self) -> None:
        """Log that the conversation continues on this agent's model (after a switch, or a
        /rewind to before one), and tell the model."""
        self.log.append(Kind.MODEL_SWITCH, {"provider": self.provider.provider, "model": self.model})
        self.log.add_message(Message.system(
            f"The conversation now continues on `{self.model}` (via {self.provider.provider}). "
            "Earlier replies may have been written by other models."))

    def interrupt(self) -> None:
        """Stop the running turn now (safe from any thread). One signal reaches everything the
        turn is doing: the model stream is closed (the provider stops generating), a running
        command is killed with everything it started, a retry wait ends. Everything produced
        so far is kept."""
        self._cancel.cancel()

    def send(self, text: str) -> None:
        """A message typed while a turn runs (safe from any thread). The model gets it at the
        next step, after the tools it asked for; a turn about to end goes on to answer it."""
        with self._inbox_lock:
            self._inbox.append(text)

    def take_inbox(self) -> list[str]:
        """The messages not given to the model yet (after a turn ends: they start the next)."""
        with self._inbox_lock:
            taken, self._inbox = self._inbox, []
        return taken

    def _deliver(self) -> bool:
        """Add the waiting messages to the conversation. True if there were any."""
        texts = self.take_inbox()
        for text in texts:
            self.log.add_message(Message.user(text))
            self.on(UserAdded(text))
        return bool(texts)

    def _check_stop(self) -> None:
        if self._cancel.cancelled:
            raise Interrupted

    def turn(self, text: str, cancel: Cancel | None = None) -> TurnEnded:
        """cancel: a stop signal to share (a child agent stops with its parent's turn)."""
        self._cancel = cancel or Cancel()
        self.log.add_message(Message.user(text))
        steps, usage = 0, Usage()
        try:
            while True:
                self._check_stop()
                if steps >= self.limits.max_steps:
                    return self._end("max_steps", steps, usage)
                if steps:
                    self._deliver()
                steps += 1
                msg = self._sample()
                usage += msg.usage or Usage()
                self.log.add_message(msg)
                self._context_changed()
                if msg.stop == "max_tokens":
                    return self._end("max_tokens", steps, usage)
                if not msg.tool_calls:
                    with self._inbox_lock:
                        waiting = bool(self._inbox)
                    if not waiting:
                        return self._end("done", steps, usage)
                    continue   # the user wrote meanwhile: answer that before ending
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

    def _summarize(self, everything: bool = False) -> bool:
        """One summary, shown as a notice. A failed summary is logged and the turn goes on
        unsummarized; an interrupt stops the turn as usual. True when a summary was logged."""
        before = self.context_use.used
        try:
            in_place = (self.system, [t.definition for t in self.tools.values()]) \
                if self.limits.in_place else None
            r = self.context_manager.summarize(self.context_use, self.provider, self.model,
                                               self._cancel, in_place, everything)
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
        """Summarize now (/compact), between turns: everything the cut may take, keeping only
        the model's latest message onward."""
        self._cancel = Cancel()
        self._context_changed()
        try:
            self._summarize(everything=True)
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
        """Run the calls in their order, except that neighbouring calls that need no approval
        and change nothing (reads, searches, read-only child agents) run at the same time.
        Results keep the calls' order either way."""
        results: list[ToolResult] = []
        try:
            i = 0
            while i < len(calls):
                self._check_stop()
                j = i
                while j < len(calls) and self._parallel_ok(calls[j]):
                    j += 1
                if j - i > 1:
                    with ThreadPoolExecutor(min(j - i, MAX_PARALLEL)) as pool:
                        results += pool.map(self._run_one, calls[i:j])
                    i = j
                else:
                    results.append(self._run_one(calls[i]))
                    i += 1
        finally:
            # results already produced are always saved, even on interrupt;
            # unanswered calls get a synthetic error when the history is sent
            if results:
                self.log.add_message(Message("tool", [*results]))

    def _parallel_ok(self, call: ToolCall) -> bool:
        """Whether a call may run alongside others: read-only, inside what's allowed without
        asking, and (for a child agent) a read-only one, which never asks either."""
        tool = self.tools.get(call.name)
        try:
            args = call.args()
        except ValueError:
            return False
        if (tool is None or call.name in self.denied or not tool.readonly
                or access(tool, args, self.paths) is not Access.INSIDE):
            return False
        return call.name != "agent" or args.get("readonly") is True

    def _run_one(self, call: ToolCall) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None or call.name in self.denied:
            usable = [n for n in self.tools if n not in self.denied]
            return ToolResult(call.id, f"Unknown tool `{call.name}`. Available: "
                                       f"{', '.join(usable)}", True)
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
            spawn = self._spawn if "agent" in self.tools and not self.child else None
            ctx = ToolContext(self.paths, self._cancel, self.log, spawn)
            result = ToolResult(call.id, tool.run(ctx, **args))
        except ToolError as e:
            result = ToolResult(call.id, str(e), True)
        except TypeError as e:
            result = ToolResult(call.id, f"Bad arguments for {call.name}: {e}", True)
        except Exception as e:  # a tool crash must not kill the session
            result = ToolResult(call.id, f"{type(e).__name__}: {e}", True)
        self.on(ToolFinished(call, result))
        return result

    def _spawn(self, task: str, readonly: bool, fork: bool = False) -> str:
        """Run `task` in a child agent (the `agent` tool) and return its final answer. Same
        model, approvals and limits; it can't start agents (depth 1); its own session log;
        stopped by this turn's stop signal; what it spends counts in this turn's usage.
        fork: it starts with this conversation as last sent, with the same system prompt and
        tools (so the provider's cache covers it), the tools it may not use refused instead."""
        log = Log.new(cwd=str(self.paths.project), parent=self.log.id, task=task, fork=fork)
        sid = log.id
        if fork:
            # the last request's messages: the view without the reply that holds this call
            for m in self.context_manager.view()[:-1]:
                log.add_message(m)
            tools = self.tools
            denied = frozenset({"agent"} | ({n for n, t in tools.items() if not t.readonly}
                                            if readonly else set()))
            system: str | Callable[[str, str], str] = self._prompt
            text = prompt.FORK.format(task=task, readonly=(
                " You may only read, search and take notes." if readonly else ""))
        else:
            tools = {n: t for n, t in self.tools.items()
                     if n != "agent" and (t.readonly or not readonly)}
            denied = frozenset()
            parent_prompt = self._prompt
            system = lambda p, m: parent_prompt(p, m) + prompt.CHILD  # noqa: E731
            text = task

        def forward(ev: UIEvent) -> None:
            if isinstance(ev, StateChanged | ToolStarted | ToolFinished | ContextSummarized
                          | TurnEnded):
                self.on(ChildEvent(sid, task, ev))

        child = Agent(self.provider, self.model, log, tools, system, approve=self.approve,
                      on=forward, limits=self.limits, sleep=self.sleep, paths=self.paths,
                      child=True, denied=denied)
        ended = child.turn(text, cancel=self._cancel)   # its end reaches the UI through `forward`
        with self._lock:                                 # children may run side by side
            self._children_usage += child.total          # counted in this turn's usage
        self.on(StateChanged("running"))
        answer = next((m.text for m in reversed(log.messages())
                       if m.role == "assistant" and m.text.strip()), "")
        how = f"{ended.steps} step{'s' if ended.steps != 1 else ''}"
        if ended.reason != "done":
            how += f", ended: {ended.reason}" + (f" ({ended.error})" if ended.error else "")
        return f"{answer or '(no answer)'}\n\n[agent: {how}; its log: {log.path.name}]"

    def _end(self, reason: Reason, steps: int, usage: Usage, error: str | None = None) -> TurnEnded:
        usage, self._children_usage = usage + self._children_usage, Usage()
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

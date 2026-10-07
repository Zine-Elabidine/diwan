"""The full-screen terminal app. The agent runs in a worker thread; its events are handed to
the app thread, which draws them. Approvals are modal dialogs the agent thread waits on."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text as RText
from tarjuman import (BlockEnd, BlockStart, Finish, ReasoningDelta, TextDelta,
                      ToolCall, ToolResult)
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TMessage
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Markdown, Static, TextArea, Tree

from . import __version__, commands
from .agent import Agent
from .events import (ChildEvent, ContextCleared, ContextSummarized, Retrying, StateChanged, ToolFinished,
                     ToolStarted, TurnEnded, UIEvent, UserAdded)
from .log import Log
from .models import Ref, Router
from .present import (child_line, fmt_context, fmt_cost, fmt_tokens, fmt_usage, preview, short,
                      summarize_call, turn_mark)
from .session import Approvals, Session
from .tools import Tool

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PREVIEW_LINES = 8

KEYS = "Enter sends · Ctrl+J new line · Esc stops the agent · Ctrl+T reasoning · Ctrl+B agents · Ctrl+Q quits"


# --- widgets ------------------------------------------------------------------------------------

class Prompt(TextArea):
    """Multi-line input: Enter sends, Ctrl+J (or Shift+Enter where the terminal supports it)
    adds a line."""

    class Submitted(TMessage):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    async def _on_key(self, event) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                self.post_message(self.Submitted(text))
                self.clear()
            return
        if event.key in ("ctrl+j", "shift+enter"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)


class UserMsg(Static):
    pass


class Footer(Static):
    pass


class ToolView(Vertical):
    """One tool call: a header line, then its output (long output collapses)."""

    def __init__(self, call: ToolCall):
        super().__init__(classes="tool")
        self.call = call
        self.header = Static(self._title("…", "cyan"))

    def _title(self, mark: str, style: str) -> RText:
        t = RText()
        t.append(f"{mark} ", style=style)
        t.append(self.call.name, style="bold")
        t.append(f"  {summarize_call(self.call)}")
        return t

    def compose(self) -> ComposeResult:
        yield self.header

    def finish(self, result: ToolResult) -> None:
        self.header.update(self._title("✗" if result.is_error else "✓",
                                       "red" if result.is_error else "green"))
        lines = [short(line) for line in (result.text.splitlines() or [""])]
        style = "red" if result.is_error else "dim"
        head = RText("\n".join(line[:300] for line in lines[:PREVIEW_LINES]), style=style)
        self.mount(Static(head, classes="tool-out"))
        if len(lines) > PREVIEW_LINES:
            rest = Static(RText("\n".join(line[:300] for line in lines[PREVIEW_LINES:]), style=style),
                          classes="tool-out")
            self.mount(Collapsible(rest, title=f"{len(lines) - PREVIEW_LINES} more lines",
                                   collapsed=True, classes="more"))


def diff_text(call: ToolCall) -> RText:
    lines = preview(call, 30)
    if lines:
        return RText("\n").join(RText(line, style=style) for line, style in lines)
    try:
        call.args()
    except ValueError:
        return RText(call.arguments[:2000])
    return RText(summarize_call(call), style="bold")


class Approval(ModalScreen[str]):
    BINDINGS: ClassVar = [Binding("y", "answer('yes')", "Yes"), Binding("a", "answer('always')", "Always"),
                Binding("n", "answer('no')", "No"), Binding("escape", "answer('no')", "No")]

    def __init__(self, call: ToolCall, outside: bool = False):
        super().__init__()
        self.call, self.outside = call, outside

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(RText.assemble(("Allow ", "bold"), (self.call.name, "bold cyan"), ("?", "bold"),
                                        ("  outside the project" if self.outside else "", "yellow")))
            with VerticalScroll(id="preview"):
                yield Static(diff_text(self.call))
            with Horizontal(id="buttons"):
                yield Button("Yes (y)", id="yes", variant="success")
                if not self.outside:
                    yield Button(f"Always {self.call.name} (a)", id="always", variant="primary")
                yield Button("No (n)", id="no", variant="error")

    def action_answer(self, answer: str) -> None:
        self.dismiss("yes" if answer == "always" and self.outside else answer)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


# --- the app ------------------------------------------------------------------------------------

class DiwanApp(App):
    TITLE = "diwan"
    CSS = """
    Screen { layout: vertical; }
    #top { height: 1; padding: 0 1; background: $panel; color: $text-muted; }
    #main { height: 1fr; }
    #chat { width: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }
    #agents { width: 32; border-left: solid $panel-lighten-2; display: none; padding: 0 1; }
    #agents.shown { display: block; }
    UserMsg { margin: 1 0 0 0; padding: 0 1; background: $boost; border-left: thick $accent; }
    Markdown { margin: 1 0 0 0; padding: 0; }
    Markdown > *:last-child { margin-bottom: 0; }
    .tool { height: auto; margin: 1 0 0 0; }
    .tool-out { padding: 0 0 0 2; }
    .more { padding: 0 0 0 1; border: none; background: transparent; }
    .reasoning { margin: 1 0 0 0; padding: 0; border: none; background: transparent; color: $text-muted; }
    Footer { color: $text-muted; margin: 1 0 0 0; }
    .notice { color: $warning; margin: 1 0 0 0; }
    #prompt { height: auto; max-height: 10; min-height: 3; border: round $accent; margin: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    Approval { align: center middle; }
    #dialog { width: 90; max-width: 95%; height: auto; max-height: 80%; padding: 1 2;
              border: thick $accent; background: $surface; }
    #preview { height: auto; max-height: 24; margin: 1 0; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin: 0 0 0 1; }
    """
    BINDINGS: ClassVar = [
        Binding("escape", "interrupt", "Stop", priority=True),
        Binding("ctrl+c", "interrupt_or_hint", "Stop", priority=True, show=False),
        Binding("ctrl+t", "toggle_think", "Reasoning"),
        Binding("ctrl+b", "toggle_agents", "Agents"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, make_agent: Callable[[Log, Ref], Agent], log: Log, cwd: Path,
                 router: Router, ref: Ref, *, approvals: Approvals | None = None,
                 show_reasoning: bool = False, first_prompt: str | None = None):
        super().__init__()
        self.cwd = cwd
        self.show_reasoning = show_reasoning
        self.first_prompt = first_prompt

        def wired(lg: Log, at: Ref) -> Agent:   # every agent, the first and after /new
            agent = make_agent(lg, at)
            agent.approve = self._approve_from_thread
            agent.on = self._from_agent
            return agent

        self.session = Session(wired, log, ref, router, cwd, approvals)
        self.running = False
        self.state = "idle"
        self._frame = 0
        self._state_since = time.monotonic()
        self._cur: dict[str, Any] | None = None
        self._tools: dict[str, ToolView] = {}
        self._children: dict[str, tuple[str, str]] = {}   # child agents: id -> (task, state)
        self._pending: set[Future[str]] = set()   # approvals the agent thread is waiting on

    @property
    def agent(self) -> Agent:
        return self.session.agent

    @property
    def session_log(self) -> Log:
        return self.session.log

    @property
    def ref(self) -> Ref:
        return self.session.ref

    # --- layout -----------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with Horizontal(id="main"):
            yield VerticalScroll(id="chat")
            yield Tree("agents", id="agents")
        yield Prompt(id="prompt", soft_wrap=True, show_line_numbers=False, compact=True)
        yield Static(id="status")

    def on_mount(self) -> None:
        self.chat = self.query_one("#chat", VerticalScroll)
        self.prompt = self.query_one("#prompt", Prompt)
        self.status = self.query_one("#status", Static)
        self._update_top()
        self._update_agents()
        self._replay_history()
        self.set_interval(0.1, self._tick)
        self.prompt.focus()
        if self.first_prompt:
            self.submit(self.first_prompt)

    def _from_agent(self, ev: object) -> None:
        """Agent events arrive from its worker thread, except during /model (main thread)."""
        if threading.current_thread() is threading.main_thread():
            self.call_later(self.handle, ev)
        else:
            self.call_from_thread(self.handle, ev)

    def _update_top(self) -> None:
        self.query_one("#top", Static).update(RText.assemble(
            ("diwan ", "bold cyan"), (f"{__version__} · ", "dim"), (str(self.ref), "bold"),
            (f" · {self.cwd} · session {self.session_log.id}", "dim")))

    def _update_agents(self) -> None:
        tree = self.query_one("#agents", Tree)
        tree.clear()
        tree.root.expand()
        main = tree.root.add(f"● main · {self.agent.model.split('/')[-1]} · {self.state}",
                             expand=True)
        for task, state in self._children.values():
            main.add_leaf(f"{'●' if state != 'done' else '○'} {short(task)[:40]} · {state}")

    def _tick(self) -> None:
        self._frame += 1
        t = RText()
        if self.state in ("thinking", "running"):
            secs = int(time.monotonic() - self._state_since)
            t.append(f"{SPINNER[self._frame % len(SPINNER)]} {self.state}", style="cyan")
            if secs >= 2:
                t.append(f" {secs}s", style="dim")
            t.append("  esc to stop", style="dim")
        elif self.state == "waiting":
            t.append("● waiting for your approval", style="yellow")
        else:
            t.append("● ready", style="green")
        spent = self.agent.total
        cost = fmt_cost(spent.cost) if spent.cost is not None else "–"
        total_in = spent.input + spent.cache_read + spent.cache_write
        t.append(f"   {fmt_tokens(total_in)} in · {fmt_tokens(spent.output)} out · {cost}",
                 style="dim")
        ctx, style = fmt_context(self.agent.context_use)
        t.append(f"   {ctx}", style=style)
        t.append("   ctrl+t reasoning · ctrl+b agents · /help", style="dim")
        self.status.update(t)

    async def _add(self, widget) -> None:
        at_bottom = self.chat.scroll_y >= self.chat.max_scroll_y - 2
        await self.chat.mount(widget)
        if at_bottom:
            self.chat.scroll_end(animate=False)

    def _follow(self) -> None:
        if self.chat.scroll_y >= self.chat.max_scroll_y - 6:
            self.chat.scroll_end(animate=False)

    # --- history ----------------------------------------------------------------------------

    def _replay_history(self) -> None:
        results = {b.call_id: b for m in self.session_log.messages() if m.role == "tool"
                   for b in m.content if isinstance(b, ToolResult)}
        for m in self.session_log.messages():
            if m.role == "user":
                self.chat.mount(UserMsg(RText(m.text)))
            elif m.role == "assistant":
                if m.text.strip():
                    self.chat.mount(Markdown(m.text))
                for c in m.tool_calls:
                    view = ToolView(c)
                    self.chat.mount(view)
                    if c.id in results:
                        view.call_after_refresh(view.finish, results[c.id])
        self.chat.call_after_refresh(self.chat.scroll_end, animate=False)

    # --- input ------------------------------------------------------------------------------

    @on(Prompt.Submitted)
    def _submitted(self, event: Prompt.Submitted) -> None:
        self.submit(event.text)

    def submit(self, text: str) -> None:
        if text.startswith("/"):
            self.command(text)
            return
        if self.running:
            self.agent.send(text)   # shown when the model gets it, at the next step
            self.notify("Queued: the model gets it after the current step. Esc stops the turn "
                        "and puts it back here.")
            return
        self.chat.mount(UserMsg(RText(text)))
        self.chat.scroll_end(animate=False)
        self.running = True
        self.run_turn(text)

    def command(self, text: str) -> None:
        reply = commands.run(self.session, text, busy=self.running)
        if reply.effect == "exit":
            self.exit()
            return
        if reply.effect == "think":
            self.action_toggle_think()
        elif reply.effect == "new":
            self.chat.remove_children()
        elif reply.effect == "compact":
            self.running = True
            self.run_compact()
        elif reply.effect == "rewind":
            self.chat.remove_children()
            self._tools.clear()
            self._replay_history()
            self.prompt.text = reply.text   # to edit and send again
            self._update_top()
            return
        if reply.effect in ("new", "model"):
            self._update_top()
            self._update_agents()
        if reply.kind == "block":
            extra = f"\n\n{KEYS}" if text.split(maxsplit=1)[0] == "/help" else ""
            self.chat.mount(Static(RText(reply.text + extra), classes="tool-out"))  # no markup
        elif reply.text:
            self.notify(reply.text, severity="error" if reply.kind == "error" else "information")
        self.chat.scroll_end(animate=False)

    @work(thread=True, exclusive=True)
    def run_turn(self, text: str) -> None:
        reason = None
        try:
            reason = self.agent.turn(text).reason
        finally:
            self.call_from_thread(self._turn_done, reason)

    @work(thread=True, exclusive=True)
    def run_compact(self) -> None:
        try:
            self.agent.compact()
        finally:
            self.call_from_thread(self._turn_done)

    def _turn_done(self, reason: str | None = None) -> None:
        left = self.agent.take_inbox()   # typed too late for the turn: it starts the next one
        if left and reason == "interrupted":
            # stopped on purpose: nothing starts by itself; the message waits in the input
            self.prompt.text = "\n\n".join([*left, *([self.prompt.text] if self.prompt.text else [])])
            left = []
        if left and self.is_running:
            text = "\n\n".join(left)
            self.chat.mount(UserMsg(RText(text)))
            self.chat.scroll_end(animate=False)
            self.run_turn(text)
            return
        self.running = False
        self.prompt.focus()

    # --- approvals --------------------------------------------------------------------------

    def _approve_from_thread(self, call: ToolCall, tool: Tool, outside: bool = False) -> bool:
        """Called on the agent thread: show the dialog on the app thread and wait for it."""
        if self.session.approvals.covers(call, outside):
            return True
        answer: Future[str] = Future()
        self._pending.add(answer)
        try:
            self.call_from_thread(self._ask, call, outside, answer)
            result = self._wait(answer)
        finally:
            self._pending.discard(answer)
        return self.session.approvals.answer(call, result, outside)

    def _ask(self, call: ToolCall, outside: bool, answer: Future[str]) -> None:
        self.push_screen(Approval(call, outside), callback=lambda r: _settle(answer, r or "no"))

    def _wait(self, answer: Future[str]) -> str:
        """Wait for the dialog, but never past the app's life: if the app closes (however it
        closes), the answer is "no" and the turn stops, so the worker thread ends."""
        while True:
            try:
                return answer.result(timeout=0.2)
            except TimeoutError:
                if not self.is_running:
                    self.agent.interrupt()
                    return "no"

    def on_unmount(self) -> None:
        for answer in list(self._pending):
            _settle(answer, "no")
        if self.running:
            self.agent.interrupt()

    # --- agent events (app thread) ----------------------------------------------------------

    async def handle(self, ev: UIEvent) -> None:
        if isinstance(ev, StateChanged):
            self.state = ev.state
            self._state_since = time.monotonic()
            self._update_agents()
            return
        if isinstance(ev, BlockStart):
            await self._close_block()
            if ev.kind == "text":
                md = Markdown("")
                await self._add(md)
                self._cur = {"kind": "text", "widget": md, "stream": Markdown.get_stream(md)}
            elif ev.kind == "reasoning":
                body = Static("", classes="reasoning-body")
                box = Collapsible(body, title="Thinking…", collapsed=not self.show_reasoning,
                                  classes="reasoning")
                await self._add(box)
                self._cur = {"kind": "reasoning", "widget": box, "body": body, "text": "",
                             "start": time.monotonic()}
            return
        if isinstance(ev, TextDelta) and self._cur and self._cur["kind"] == "text":
            await self._cur["stream"].write(ev.text)
            self._follow()
            return
        if isinstance(ev, ReasoningDelta) and self._cur and self._cur["kind"] == "reasoning":
            self._cur["text"] += ev.text
            self._cur["body"].update(RText(self._cur["text"], style="dim italic"))
            self._follow()
            return
        if isinstance(ev, BlockEnd | Finish):
            await self._close_block()
            return
        if isinstance(ev, ToolStarted):
            if ev.call.id not in self._tools:
                view = ToolView(ev.call)
                self._tools[ev.call.id] = view
                await self._add(view)
        elif isinstance(ev, ToolFinished):
            view = self._tools.get(ev.call.id)
            if view is None:
                view = ToolView(ev.call)
                self._tools[ev.call.id] = view
                await self._add(view)
            view.finish(ev.result)
            self._follow()
        elif isinstance(ev, ContextCleared):
            await self._add(Static(f"↺ {ev.text}; the log keeps them", classes="notice"))
        elif isinstance(ev, ContextSummarized):
            await self._add(Static(f"↺ {ev.text}", classes="notice"))
        elif isinstance(ev, ChildEvent):
            e = ev.event
            if isinstance(e, StateChanged | TurnEnded):
                state = e.state if isinstance(e, StateChanged) else "done"
                self._children[ev.id] = (ev.task, state)
                self._update_agents()
            line = child_line(ev)
            if line:
                await self._add(Static(RText(f"  {line}"), classes="notice"))
        elif isinstance(ev, UserAdded):
            await self._close_block()
            await self._add(UserMsg(RText(ev.text)))
        elif isinstance(ev, Retrying):
            await self._add(Static(f"{ev.error.code}, retrying in {ev.wait:.0f}s "
                                   f"(attempt {ev.attempt})", classes="notice"))
        elif isinstance(ev, TurnEnded):
            await self._close_block()
            mark = turn_mark(ev.reason)
            t = RText.assemble(mark, (f"  {ev.steps} step{'s' if ev.steps != 1 else ''} · "
                                      f"{fmt_usage(ev.usage)}", "dim"))
            if ev.error:
                t.append(f"\n{ev.error}", style="red")
            await self._add(Footer(t))

    async def _close_block(self) -> None:
        cur, self._cur = self._cur, None
        if not cur:
            return
        if cur["kind"] == "text":
            await cur["stream"].stop()
        elif cur["kind"] == "reasoning":
            secs = time.monotonic() - cur["start"]
            cur["widget"].title = f"Thought for {secs:.0f}s" if secs >= 1 else "Thought briefly"

    # --- actions ----------------------------------------------------------------------------

    def action_interrupt(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss("no")
            return
        if self.running:
            self.agent.interrupt()
            self.notify("Stopping…")

    def action_interrupt_or_hint(self) -> None:
        if self.running:
            self.action_interrupt()
        else:
            self.notify("Ctrl+Q quits.")

    def action_toggle_think(self) -> None:
        self.show_reasoning = not self.show_reasoning
        for box in self.query(".reasoning").results(Collapsible):
            box.collapsed = not self.show_reasoning
        self.notify(f"reasoning {'shown' if self.show_reasoning else 'hidden'}")

    def action_toggle_agents(self) -> None:
        self.query_one("#agents").toggle_class("shown")


def _settle(answer: Future[str], value: str) -> None:
    if not answer.done():
        answer.set_result(value)

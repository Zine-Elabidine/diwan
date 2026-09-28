"""The terminal: `diwan` for a chat, `diwan -p "task"` for one shot."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tarjuman import (BlockStart, ReasoningDelta, TarjumanError, TextDelta, ToolCall, Usage)
from tarjuman import providers

from . import __version__
from .agent import (Agent, Retrying, StateChanged, ToolFinished, ToolStarted, TurnEnded,
                    UIEvent)
from .log import Log
from .prompt import system_prompt
from .tools import Spec, make_tools

DEFAULT_MODEL = "z-ai/glm-5.3-flash"
DIM, RED, GREEN, CYAN, BOLD, RESET = "\033[2m", "\033[31m", "\033[32m", "\033[36m", "\033[1m", "\033[0m"
CLEAR = "\r\033[K"

HELP = """/model <id>   switch model (any OpenRouter id, e.g. deepseek/deepseek-v4-flash)
/cost         tokens and cost for this session
/new          start a new session
/exit         quit (or Ctrl-D).  Ctrl-C stops the agent mid-turn."""


def _color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if _color() else text


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def fmt_usage(u: Usage) -> str:
    parts = [f"{fmt_tokens(u.input + u.cache_read + u.cache_write)} in"]
    if u.cache_read:
        parts[-1] += f" ({fmt_tokens(u.cache_read)} cached)"
    parts.append(f"{fmt_tokens(u.output)} out")
    if u.cost is not None:
        parts.append(f"${u.cost:.4f}")
    return " · ".join(parts)


def summarize_call(call: ToolCall) -> str:
    try:
        a = call.args()
    except ValueError:
        return call.arguments[:80]
    if call.name == "bash":
        return a.get("command", "")
    if call.name == "read" and a.get("offset"):
        return f"{a.get('path', '')}:{a['offset']}"
    return str(a.get("path", ""))


class Terminal:
    """Renders loop events and asks for approvals."""

    def __init__(self, auto_approve: bool = False, interactive: bool = True):
        self.always: set[str] = set()
        self.auto = auto_approve
        self.interactive = interactive
        self.spinner = False
        self.kind: str | None = None   # kind of block being streamed
        self.shown: str | None = None  # call already printed by the approval prompt

    def _clear_spinner(self) -> None:
        if self.spinner:
            sys.stdout.write(CLEAR)
            self.spinner = False

    def on(self, ev: UIEvent) -> None:
        if isinstance(ev, StateChanged):
            if ev.state == "thinking" and _color():
                sys.stdout.write(paint(DIM, "… thinking"))
                sys.stdout.flush()
                self.spinner = True
            return
        self._clear_spinner()
        if isinstance(ev, BlockStart):
            if self.kind is not None:
                sys.stdout.write("\n")
            self.kind = ev.kind if ev.kind != "tool_call" else None
        elif isinstance(ev, ReasoningDelta):
            sys.stdout.write(paint(DIM, ev.text))
        elif isinstance(ev, TextDelta):
            sys.stdout.write(ev.text)
        elif isinstance(ev, ToolStarted):
            self._end_block()
            if ev.call.id != self.shown:
                print(f"{paint(CYAN, '●')} {paint(BOLD, ev.call.name)} {summarize_call(ev.call)}")
        elif isinstance(ev, ToolFinished):
            self._end_block()
            lines = ev.result.content.splitlines() or [""]
            color = RED if ev.result.is_error else DIM
            shown = lines[:4]
            for line in shown:
                print(paint(color, f"  │ {line[:160]}"))
            if len(lines) > len(shown):
                print(paint(DIM, f"  │ … {len(lines) - len(shown)} more lines"))
        elif isinstance(ev, Retrying):
            self._end_block()
            print(paint(RED, f"  {ev.error.code}, retrying in {ev.wait:.0f}s (attempt {ev.attempt})"))
        elif isinstance(ev, TurnEnded):
            self._end_block()
            mark = {"done": paint(GREEN, "✓"), "interrupted": paint(RED, "■ interrupted")}.get(
                ev.reason, paint(RED, f"■ stopped: {ev.reason}"))
            print(paint(DIM, f"{mark} {ev.steps} step{'s' if ev.steps != 1 else ''} · {fmt_usage(ev.usage)}"))
            if ev.error:
                print(paint(RED, f"  {ev.error}"))
        sys.stdout.flush()

    def _end_block(self) -> None:
        if self.kind is not None:
            sys.stdout.write("\n")
            self.kind = None

    def approve(self, call: ToolCall, spec: Spec) -> bool:
        self._clear_spinner()
        self._end_block()
        print(f"{paint(CYAN, '●')} {paint(BOLD, call.name)} {summarize_call(call)}")
        self.shown = call.id
        if call.name in ("write", "edit"):
            preview(call)
        if self.auto or call.name in self.always:
            return True
        if not self.interactive:
            return False
        while True:
            try:
                answer = input(f"  allow? [y]es / [n]o / [a]lways {call.name} › ").strip().lower()
            except EOFError:
                return False
            if answer in ("y", "yes", ""):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "always"):
                self.always.add(call.name)
                return True


def preview(call: ToolCall) -> None:
    try:
        a = call.args()
    except ValueError:
        return
    if call.name == "edit":
        for line in str(a.get("old", "")).splitlines()[:8]:
            print(paint(RED, f"  - {line}"))
        for line in str(a.get("new", "")).splitlines()[:8]:
            print(paint(GREEN, f"  + {line}"))
    else:
        lines = str(a.get("content", "")).splitlines()
        for line in lines[:6]:
            print(paint(GREEN, f"  + {line}"))
        if len(lines) > 6:
            print(paint(DIM, f"  … {len(lines) - 6} more lines"))


def build_agent(model: str, log: Log, term: Terminal, cwd: Path, base_url: str | None) -> Agent:
    provider = providers.local(base_url) if base_url else providers.openrouter()
    return Agent(provider, model, log, make_tools(cwd), system_prompt(cwd),
                 approve=term.approve, on=term.on)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="diwan", description="A coding agent that keeps a record of everything.")
    ap.add_argument("-m", "--model", default=os.environ.get("DIWAN_MODEL", DEFAULT_MODEL))
    ap.add_argument("-p", "--print", dest="prompt", help="run one task and exit")
    ap.add_argument("-r", "--resume", nargs="?", const="last", help="resume the last session here, or a session file")
    ap.add_argument("--base-url", default=os.environ.get("DIWAN_BASE_URL"),
                    help="an OpenAI-compatible server instead of OpenRouter (vLLM, llama.cpp...)")
    ap.add_argument("-y", "--yes", action="store_true", help="approve every tool call")
    ap.add_argument("--version", action="version", version=f"diwan {__version__}")
    args = ap.parse_args(argv)

    cwd = Path.cwd()
    if args.resume == "last":
        log = Log.latest(str(cwd))
        if log is None:
            print("No earlier session in this folder.", file=sys.stderr)
            return 1
    elif args.resume:
        log = Log.load(Path(args.resume).expanduser())
    else:
        log = Log.new(cwd=str(cwd), model=args.model, diwan=__version__)

    term = Terminal(auto_approve=args.yes, interactive=args.prompt is None)
    try:
        agent = build_agent(args.model, log, term, cwd, args.base_url)
    except TarjumanError as e:
        print(paint(RED, str(e)), file=sys.stderr)
        return 1

    if args.prompt:
        ended = agent.turn(args.prompt)
        return 0 if ended.reason == "done" else 1

    try:
        import readline  # noqa: F401  line editing and history for input()
    except ImportError:
        pass
    print(paint(DIM, f"diwan {__version__} · {args.model} · session {log.id} · /help"))
    while True:
        try:
            text = input(paint(BOLD, "› ")).strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not text:
            continue
        if text.startswith("/"):
            cmd, _, rest = text.partition(" ")
            if cmd in ("/exit", "/quit"):
                return 0
            elif cmd == "/help":
                print(HELP)
            elif cmd == "/model":
                if rest.strip():
                    agent.model = rest.strip()
                    log.append("model", {"model": agent.model})
                print(paint(DIM, f"model: {agent.model}"))
            elif cmd == "/cost":
                print(paint(DIM, fmt_usage(agent.total)))
            elif cmd == "/new":
                log = Log.new(cwd=str(cwd), model=agent.model, diwan=__version__)
                agent = build_agent(agent.model, log, term, cwd, args.base_url)
                print(paint(DIM, f"new session {log.id}"))
            else:
                print(f"unknown command {cmd}; /help")
            continue
        agent.turn(text)


if __name__ == "__main__":
    sys.exit(main())

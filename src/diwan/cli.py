"""The command line: `diwan` for a chat, `diwan -p "task"` for one shot."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.text import Text

from tarjuman import TarjumanError
from tarjuman import providers

from . import __version__
from .agent import Agent
from .log import Log
from .prompt import system_prompt
from .tools import make_tools
from .ui import Terminal, fmt_usage

DEFAULT_MODEL = "deepseek/deepseek-v4-flash"

HELP = """[bold]/model[/bold] <id>   switch model (any OpenRouter id, e.g. z-ai/glm-5.3-flash)
[bold]/think[/bold]        show or hide the model's reasoning
[bold]/cost[/bold]         tokens and cost for this session
[bold]/new[/bold]          start a new session
[bold]/exit[/bold]         quit (or Ctrl-D).  Ctrl-C stops the agent mid-turn."""

# readline miscounts the prompt width unless color codes are wrapped in \001..\002
PROMPT = "\001\033[1;36m\002› \001\033[0m\002"


def load_env_file() -> None:
    """Read KEY=value lines from ~/.diwan/env (keep it chmod 600). Real env vars win."""
    path = Path(os.environ.get("DIWAN_HOME", Path.home() / ".diwan")) / "env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_agent(model: str, log: Log, term: Terminal, cwd: Path, base_url: str | None) -> Agent:
    provider = providers.local(base_url) if base_url else providers.openrouter()
    return Agent(provider, model, log, make_tools(cwd),
                 system_prompt(cwd, model, provider.provider),
                 approve=term.approve, on=term.on)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    ap = argparse.ArgumentParser(prog="diwan", description="A coding agent that keeps a record of everything.")
    ap.add_argument("-m", "--model", default=os.environ.get("DIWAN_MODEL", DEFAULT_MODEL))
    ap.add_argument("-p", "--print", dest="prompt", help="run one task and exit")
    ap.add_argument("-r", "--resume", nargs="?", const="last", help="resume the last session here, or a session file")
    ap.add_argument("--base-url", default=os.environ.get("DIWAN_BASE_URL"),
                    help="an OpenAI-compatible server instead of OpenRouter (vLLM, llama.cpp...)")
    ap.add_argument("-y", "--yes", action="store_true", help="approve every tool call")
    ap.add_argument("--think", action="store_true", help="show the model's reasoning")
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

    term = Terminal(auto_approve=args.yes, interactive=args.prompt is None,
                    show_reasoning=args.think)
    c = term.console
    try:
        agent = build_agent(args.model, log, term, cwd, args.base_url)
    except TarjumanError as e:
        c.print(Text(str(e), style="red"))
        return 1

    if args.prompt:
        ended = agent.turn(args.prompt)
        return 0 if ended.reason == "done" else 1

    try:
        import readline  # noqa: F401  line editing and history for input()
    except ImportError:
        pass
    c.print(f"[bold cyan]diwan[/bold cyan] [dim]{__version__} · {args.model} · "
            f"{cwd} · /help[/dim]\n")
    while True:
        try:
            text = input(PROMPT).strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not text:
            continue
        if not text.startswith("/"):
            c.print()
            agent.turn(text)
            continue
        cmd, _, rest = text.partition(" ")
        rest = rest.strip()
        if cmd in ("/exit", "/quit"):
            return 0
        elif cmd == "/help":
            c.print(HELP)
        elif cmd == "/model":
            if rest:
                agent.model = rest
                agent.system = system_prompt(cwd, rest, agent.provider.provider)
                log.append("model", {"model": rest})
            c.print(f"[dim]model: {agent.model}[/dim]")
        elif cmd == "/think":
            term.show_reasoning = not term.show_reasoning
            c.print(f"[dim]reasoning {'shown' if term.show_reasoning else 'hidden'}[/dim]")
        elif cmd == "/cost":
            c.print(f"[dim]{fmt_usage(agent.total)}[/dim]")
        elif cmd == "/new":
            log = Log.new(cwd=str(cwd), model=agent.model, diwan=__version__)
            agent = build_agent(agent.model, log, term, cwd, args.base_url)
            c.print(f"[dim]new session {log.id}[/dim]")
        else:
            c.print(f"[dim]unknown command {cmd}; /help[/dim]")
        c.print()


if __name__ == "__main__":
    sys.exit(main())

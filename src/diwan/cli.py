"""The command line: `diwan` for a chat, `diwan -p "task"` for one shot."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.text import Text
from tarjuman import TarjumanError, errors, providers

from . import __version__
from .agent import Agent, Limits
from .log import Log
from .models import Ref, Router, describe, listing, switch
from .prompt import system_prompt
from .tools import make_tools
from .ui import Terminal, fmt_usage

# the model used when none is given, per provider ("local" has none: say which with -m)
DEFAULT_MODELS = {"openrouter": "deepseek/deepseek-v4-flash", "anthropic": "claude-sonnet-5-5",
                  "deepseek": "deepseek-v4-flash", "openai": "gpt-5"}

HELP = """[bold]/model[/bold] [provider:]<id>   switch model, even across providers, mid-conversation
                       (anthropic:claude-sonnet-5-5, openrouter:z-ai/glm-5.3-flash, deepseek:...)
[bold]/models[/bold] [provider] [text]  models in the catalog, cheapest first
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


def build_agent(ref: Ref, log: Log, term: Terminal, cwd: Path, router: Router,
                limits: Limits | None = None) -> Agent:
    client = router.client(ref.provider)
    return Agent(client, ref.model, log, make_tools(cwd),
                 system_prompt(cwd, ref.model, client.provider),
                 approve=term.approve, on=term.on, limits=limits)


def start_ref(args: argparse.Namespace, log: Log | None, router: Router) -> Ref:
    """The model to start on: -m, else the one a resumed session was last using, else the
    provider's default."""
    if args.model:
        return router.ref(args.model)
    if log is not None:
        provider, model = log.current_model()
        if model:
            return Ref(provider or router.default, model)
    model = DEFAULT_MODELS.get(router.default)
    if model is None:
        raise TarjumanError(errors.INVALID_REQUEST,
                            f"say which model {router.default} should run: -m <id>")
    return Ref(router.default, model)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    ap = argparse.ArgumentParser(prog="diwan",
                                 description="A coding agent that keeps a record of everything.")
    ap.add_argument("-m", "--model", default=os.environ.get("DIWAN_MODEL"),
                    help="[provider:]model, e.g. anthropic:claude-sonnet-5-5")
    ap.add_argument("--provider", choices=providers.names(), default=os.environ.get("DIWAN_PROVIDER"),
                    help="the provider for model ids without a prefix (default: openrouter, "
                         "or local with --base-url)")
    ap.add_argument("-p", "--print", dest="prompt", help="run one task and exit")
    ap.add_argument("-r", "--resume", nargs="?", const="last",
                    help="resume the last session here, or a session file")
    ap.add_argument("--base-url", default=os.environ.get("DIWAN_BASE_URL"),
                    help="point the provider elsewhere: an OpenAI-compatible server (vLLM, "
                         "llama.cpp, a gateway such as Bifrost), or an Anthropic-compatible one "
                         "with --provider anthropic")
    ap.add_argument("-y", "--yes", action="store_true", help="approve every tool call")
    ap.add_argument("--think", action="store_true", help="show the model's reasoning")
    ap.add_argument("--plain", action="store_true", help="simple line mode instead of the full-screen app")
    ap.add_argument("--context", type=int, metavar="TOKENS",
                    help="cap the context window (also used when a model's window is unknown)")
    ap.add_argument("--version", action="version", version=f"diwan {__version__}")
    args = ap.parse_args(argv)
    limits = Limits(context=args.context)

    cwd = Path.cwd()
    log: Log | None = None
    if args.resume == "last":
        log = Log.latest(str(cwd))
        if log is None:
            print("No earlier session in this folder.", file=sys.stderr)
            return 1
    elif args.resume:
        log = Log.load(Path(args.resume).expanduser())

    router = Router(args.provider or ("local" if args.base_url else "openrouter"), args.base_url)
    term = Terminal(auto_approve=args.yes, interactive=args.prompt is None,
                    show_reasoning=args.think)
    c = term.console
    try:
        ref = start_ref(args, log, router)
        if log is None:
            log = Log.new(cwd=str(cwd), provider=ref.provider, model=ref.model, diwan=__version__)
        agent = build_agent(ref, log, term, cwd, router, limits)
    except TarjumanError as e:
        c.print(Text(str(e), style="red"))
        return 1

    if args.prompt:
        ended = agent.turn(args.prompt)
        return 0 if ended.reason == "done" else 1

    if not args.plain and sys.stdin.isatty() and sys.stdout.isatty():
        from .tui import DiwanApp

        def make_agent(new_log: Log, at: Ref) -> Agent:
            return build_agent(at, new_log, term, cwd, router, limits)

        DiwanApp(make_agent, log, cwd, router, ref, show_reasoning=args.think).run()
        return 0

    try:
        import readline  # noqa: F401  line editing and history for input()
    except ImportError:
        pass
    c.print(f"[bold cyan]diwan[/bold cyan] [dim]{__version__} · {ref} · {cwd} · /help[/dim]\n")
    while True:
        try:
            text = input(PROMPT if sys.stdout.isatty() else "› ").strip()
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
            try:
                if rest:
                    ref = switch(agent, router, rest, cwd)
                c.print(Text(describe(ref), style="dim"))
            except TarjumanError as e:
                c.print(Text(str(e), style="red"))
        elif cmd == "/models":
            c.print(Text("\n".join(listing(rest, ref.provider)), style="dim"))
        elif cmd == "/think":
            term.show_reasoning = not term.show_reasoning
            c.print(f"[dim]reasoning {'shown' if term.show_reasoning else 'hidden'}[/dim]")
        elif cmd == "/cost":
            c.print(f"[dim]{fmt_usage(agent.total)}[/dim]")
        elif cmd == "/new":
            log = Log.new(cwd=str(cwd), provider=ref.provider, model=ref.model, diwan=__version__)
            agent = build_agent(ref, log, term, cwd, router, limits)
            c.print(f"[dim]new session {log.id}[/dim]")
        else:
            c.print(f"[dim]unknown command {cmd}; /help[/dim]")
        c.print()


if __name__ == "__main__":
    sys.exit(main())

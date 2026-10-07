"""The command line: `diwan` for a chat, `diwan -p "task"` for one shot."""

from __future__ import annotations

import argparse
import atexit
import os
import sys
from pathlib import Path
from typing import TextIO

from rich.text import Text
from tarjuman import TarjumanError, Usage, errors, providers

from . import __version__, commands, mcp, memory, sandbox, skills
from .agent import Agent, Limits
from .log import Log
from .models import Ref, Router
from .paths import PathPolicy
from .prompt import system_prompt
from .session import Approvals, Session
from .tools import default_tools
from .ui import Terminal

# the model used when none is given, per provider ("local" has none: say which with -m)
DEFAULT_MODELS = {"openrouter": "deepseek/deepseek-v4-flash", "anthropic": "claude-sonnet-5-5",
                  "deepseek": "deepseek-v4-flash", "openai": "gpt-5"}

KEYS = "Ctrl-D quits. Ctrl-C stops the agent mid-turn."

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
    mem = memory.current
    paths = PathPolicy(cwd, readable=[*(mem.readable() if mem else []),
                                      *skills.readable(skills.current)])
    tools = {**default_tools(sandbox.current), **({"memory": memory.Remember()} if mem else {}),
             **(mcp.current.tools if mcp.current else {})}
    extra = skills.prompt_section(skills.current) + (memory.prompt_section(mem) if mem else "")
    return Agent(client, ref.model, log, tools,
                 lambda provider, model: system_prompt(cwd, model, provider) + extra,
                 approve=term.approve, on=term.on, limits=limits, paths=paths)


def start_mcp(out: TextIO) -> None:
    """Start the servers in ~/.diwan/mcp.json; problems are printed, never fatal."""
    try:
        config = mcp.load_config()
    except mcp.MCPError as e:
        print(f"MCP: {e}", file=out)
        return
    if not config:
        return
    mcp.current = mcp.connect(config)
    atexit.register(mcp.current.close)
    for name, why in mcp.current.errors.items():
        print(f"MCP server {name!r} didn't start: {why}", file=out)


def start_memory(cwd: Path, out: TextIO) -> None:
    """Load this project's memory through Telepathy, and save it back when Diwan exits."""
    memory.current, message = memory.start(cwd)
    if message:
        print(message, file=out)
    if memory.current is not None:
        atexit.register(memory.end, cwd)


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
    ap.add_argument("--no-summaries", action="store_true",
                    help="never summarize the oldest messages automatically (/compact still works)")
    ap.add_argument("--sandbox", action="store_true",
                    help="run bash in a sandbox (Linux, bubblewrap): writes only in the project, "
                         "/tmp and ~/.cache, credentials hidden; sandboxed commands don't ask")
    ap.add_argument("--no-network", action="store_true",
                    help="with --sandbox: no network for commands either")
    ap.add_argument("--no-memory", action="store_true",
                    help="don't load or save memory (Telepathy) in this session")
    ap.add_argument("--version", action="version", version=f"diwan {__version__}")
    args = ap.parse_args(argv)
    limits = Limits(context=args.context, summaries=not args.no_summaries)

    cwd = Path.cwd()
    log: Log | None = None
    if args.resume == "last":
        log = Log.latest(str(cwd))
        if log is None:
            print("No earlier session in this folder.", file=sys.stderr)
            return 1
    elif args.resume:
        log = Log.load(Path(args.resume).expanduser())

    if args.sandbox:
        why = sandbox.available()
        if why:
            print(f"diwan: {why}", file=sys.stderr)
            return 1
        sandbox.current = sandbox.Sandbox(cwd, Path.home(), network=not args.no_network)
    elif args.no_network:
        print("diwan: --no-network works with --sandbox", file=sys.stderr)
        return 1
    start_mcp(sys.stderr)
    skills.current = skills.find(cwd)
    if not args.no_memory:
        start_memory(cwd, sys.stderr)
    router = Router(args.provider or ("local" if args.base_url else "openrouter"), args.base_url)
    approvals = Approvals(auto=args.yes)
    term = Terminal(approvals=approvals, interactive=args.prompt is None,
                    show_reasoning=args.think)
    c = term.console
    try:
        ref = start_ref(args, log, router)
        if log is None:
            log = Log.new(cwd=str(cwd), provider=ref.provider, model=ref.model, diwan=__version__)
        session = Session(lambda lg, at: build_agent(at, lg, term, cwd, router, limits), log, ref,
                          router, cwd, approvals)
    except TarjumanError as e:
        c.print(Text(str(e), style="red"))
        return 1

    if args.prompt:
        ended = session.agent.turn(args.prompt)
        return 0 if ended.reason == "done" else 1

    if not args.plain and sys.stdin.isatty() and sys.stdout.isatty():
        from .tui import DiwanApp

        def make_agent(new_log: Log, at: Ref) -> Agent:
            return build_agent(at, new_log, term, cwd, router, limits)

        DiwanApp(make_agent, log, cwd, router, ref, approvals=approvals,
                 show_reasoning=args.think).run()
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
            session.agent.turn(text)
            continue
        reply = commands.run(session, text)
        if reply.effect == "exit":
            return 0
        if reply.effect == "new":
            term.session = Usage()   # the footer's session total starts again, like /cost
        if reply.effect == "compact":
            session.agent.compact()
        if reply.effect == "rewind":
            c.print(Text(f"rewound to before: {reply.text}", style="dim"))
            continue
        if reply.effect == "think":
            term.show_reasoning = not term.show_reasoning
            reply.text = f"reasoning {'shown' if term.show_reasoning else 'hidden'}"
        if reply.text:
            c.print(Text(reply.text, style="red" if reply.kind == "error" else "dim"))
        if text.split()[0] == "/help":
            c.print(Text(KEYS, style="dim"))
        c.print()

if __name__ == "__main__":
    sys.exit(main())

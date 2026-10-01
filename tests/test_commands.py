"""Slash commands in both front-ends, as they behave today: these pin the behaviour before the
commands move into one registry (phase 3 of docs/code-review-2026-10-01.md). Where the two
front-ends disagree, the test says so; fixing that is a separate, visible change."""

import builtins

import pytest
from textual.widgets import Collapsible
from tarjuman import Message, Reasoning, Text, Usage, providers
from tarjuman.fake import Fake

from diwan import cli
from diwan.agent import Agent
from diwan.log import Log
from diwan.models import Ref, Router
from diwan.tools import make_tools
from diwan.tui import DiwanApp


def paid(text, cost):
    return Message("assistant", [Text(text)], usage=Usage(1_000, 0, 0, 10, 0, cost))


# --- plain mode -------------------------------------------------------------------------------

def run_plain(monkeypatch, tmp_path, lines, scripts):
    """Run `diwan --plain` on scripted input lines; each provider name gets a Fake."""
    clients = {name: Fake(list(script)) for name, script in scripts.items()}
    for name, f in clients.items():
        f.provider = name
    monkeypatch.setattr(providers, "connect", lambda name, **kw: clients[name])
    monkeypatch.chdir(tmp_path)
    feed = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", fake_input)
    code = cli.main(["--plain", "-y", "-m", "openrouter:deepseek/deepseek-v4-flash"])
    return code, clients


def test_plain_commands(monkeypatch, tmp_path, capsys):
    code, _ = run_plain(monkeypatch, tmp_path, [
        "/help", "/model", "/think", "/nonsense", "/models deepseek", "/exit", "never read"],
        {"openrouter": []})
    out = capsys.readouterr().out
    assert code == 0
    assert "/model" in out and "/cost" in out and "Ctrl-C stops the agent" in out   # /help
    assert "openrouter:deepseek/deepseek-v4-flash" in out                             # /model
    assert "reasoning shown" in out                                                   # /think
    assert "unknown command /nonsense; /help" in out
    assert "deepseek" in out.split("reasoning shown")[1]                              # /models


def test_plain_model_switch_and_cost(monkeypatch, tmp_path, capsys):
    code, clients = run_plain(monkeypatch, tmp_path, [
        "hi", "/model anthropic:claude-x", "again", "/cost", "/new", "/cost"],
        {"openrouter": [paid("one", 0.25)], "anthropic": [paid("two", 0.5)]})
    out = capsys.readouterr().out
    assert code == 0                                   # EOF ends the session
    assert clients["anthropic"].requests                # the second turn went to the new model
    costs = [line for line in out.splitlines() if " out" in line and "in ·" in line
             and "step" not in line]
    # /cost in plain mode: what this agent spent (both models), and zero after /new
    assert "$0.75" in costs[0]
    assert "new session" in out
    assert "$" not in costs[1]


def test_plain_unknown_prefix_is_a_model_id_on_the_current_provider(monkeypatch, tmp_path, capsys):
    # "nowhere" isn't a provider, so the whole text is a model id (OpenRouter ids can hold ":")
    run_plain(monkeypatch, tmp_path, ["/model nowhere:x-1", "/exit"], {"openrouter": []})
    assert "openrouter:nowhere:x-1" in capsys.readouterr().out


# --- full-screen app --------------------------------------------------------------------------

def make_app(tmp_path, script):
    def make_agent(log, ref):
        return Agent(Fake(list(script)), ref.model, log, make_tools(tmp_path), "sys")
    return DiwanApp(make_agent, Log.new(cwd=str(tmp_path)), tmp_path, Router("openrouter"),
                    Ref("openrouter", "deepseek/deepseek-v4-flash"))


async def settle(pilot, app):
    for _ in range(100):
        await pilot.pause(0.05)
        if not app.running:
            return
    raise AssertionError("agent never finished")


@pytest.mark.asyncio
async def test_tui_commands(tmp_path):
    app = make_app(tmp_path, [paid("hello", 0.25)])
    notes: list[str] = []
    app.notify = lambda message, **kw: notes.append(str(message))
    async with app.run_test(size=(110, 40)) as pilot:
        app.submit("/help")
        await pilot.pause(0.1)
        assert "Ctrl+Q" in app.chat.children[-1].source          # its own help text
        app.submit("/model")
        assert notes[-1].startswith("openrouter:deepseek/deepseek-v4-flash")
        app.submit("/nonsense")
        assert notes[-1] == "unknown command /nonsense (/help)"
        app.submit("/cost")
        assert notes[-1] == "0 in · 0 out"     # its "nothing spent yet" fallback never shows
        app.submit("hi")
        await settle(pilot, app)
        app.submit("/cost")
        assert "$0.25" in notes[-1]
        first = app.session_log.id
        app.submit("/new")
        await pilot.pause(0.1)
        assert app.session_log.id != first and notes[-1].startswith("new session")
        app.submit("/cost")
        # unlike plain mode, the app's /cost counts the whole app run, across /new
        assert "$0.25" in notes[-1]


@pytest.mark.asyncio
async def test_tui_model_switch_and_reasoning_toggle(tmp_path, monkeypatch):
    other = Fake([])
    other.provider = "anthropic"
    monkeypatch.setattr(providers, "connect", lambda name, **kw: other)
    app = make_app(tmp_path, [Message("assistant", [Reasoning("pondering"), Text("done")],
                                      usage=Usage(10, 0, 0, 5))])
    notes: list[str] = []
    app.notify = lambda message, **kw: notes.append(str(message))
    async with app.run_test(size=(110, 40)) as pilot:
        app.submit("hi")
        await settle(pilot, app)
        boxes = list(app.query(".reasoning").results(Collapsible))
        assert boxes and all(b.collapsed for b in boxes)           # hidden by default
        app.submit("/think")
        assert all(not b.collapsed for b in boxes) and notes[-1] == "reasoning shown"
        await pilot.press("ctrl+t")
        assert all(b.collapsed for b in boxes)
        app.submit("/model anthropic:claude-x")
        assert app.ref == Ref("anthropic", "claude-x") and app.agent.model == "claude-x"
        assert notes[-1].startswith("anthropic:claude-x")

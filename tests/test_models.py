import argparse

import pytest
from tarjuman import Message, Reasoning, Replay, TarjumanError, Text, providers
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.cli import start_ref
from diwan.log import Log
from diwan.models import Ref, Router, describe, listing, parse, switch
from diwan.prompt import system_prompt
from diwan.tools import default_tools


def fake(name, *script):
    f = Fake(list(script))
    f.provider = name
    return f


def test_parse_references():
    assert parse("anthropic:claude-x", "openrouter") == Ref("anthropic", "claude-x")
    assert parse("claude-x", "anthropic") == Ref("anthropic", "claude-x")
    # OpenRouter ids can contain a colon: not a provider prefix
    assert parse("deepseek/deepseek-r1:free", "openrouter") == Ref("openrouter", "deepseek/deepseek-r1:free")
    assert str(Ref("deepseek", "deepseek-v4-pro")) == "deepseek:deepseek-v4-pro"


def test_router_keeps_one_client_per_provider_and_reports_missing_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = Router("openrouter")
    assert r.client("openrouter") is r.client("openrouter")
    with pytest.raises(TarjumanError, match="ANTHROPIC_API_KEY"):
        r.client("anthropic")
    local = Router("local", "http://localhost:9000/v1").client("local")
    assert local.base_url == "http://localhost:9000/v1"


def test_one_conversation_three_models(tmp_path, monkeypatch):
    """Alpha thinks and answers; beta continues and sees alpha's thinking as text; back on
    alpha, its own thinking returns with its replay data."""
    alpha = fake("alpha",
                 Message("assistant", [Reasoning("alpha plan"), Text("hello from alpha")],
                         replay=Replay(None, [{"signature": "A-SIG"}, None])),
                 Message("assistant", [Text("alpha again")]))
    beta = fake("beta", Message("assistant", [Text("hello from beta")]))
    clients = {"alpha": alpha, "beta": beta}
    monkeypatch.setattr(providers, "connect", lambda name, **kw: clients[name])
    monkeypatch.setattr(providers, "names", lambda: ["alpha", "beta"])

    router = Router("alpha")
    log = Log.new(cwd=str(tmp_path), provider="alpha", model="a-1")
    agent = Agent(router.client("alpha"), "a-1", log, default_tools(),
                  lambda provider, model: system_prompt(tmp_path, model, provider))
    agent.turn("hi")

    assert switch(agent, router, "beta:b-1") == Ref("beta", "b-1")
    agent.turn("and you?")
    sent = beta.requests[-1]
    assert "`b-1`" in sent[0].text                                         # its own system prompt
    alpha_turn = sent[2]
    assert alpha_turn.content == [Text("alpha plan"), Text("hello from alpha")]   # thinking as text
    assert alpha_turn.replay is None                                       # alpha's data never leaks
    assert "now continues on `b-1`" in sent[3].text                        # the switch, as a reminder

    switch(agent, router, "alpha:a-1")
    agent.turn("back to you")
    back = alpha.requests[-1]
    own = next(m for m in back if m.role == "assistant" and m.provider == "alpha")
    assert own.content[0] == Reasoning("alpha plan") and own.replay.blocks[0] == {"signature": "A-SIG"}
    assert log.current_model() == ("alpha", "a-1")


def test_failed_switch_leaves_the_agent_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    router = Router("openrouter")
    log = Log.new(cwd=str(tmp_path))
    agent = Agent(router.client("openrouter"), "m", log, default_tools(), "sys")
    with pytest.raises(TarjumanError):
        switch(agent, router, "anthropic:claude-x")
    assert (agent.provider.provider, agent.model) == ("openrouter", "m")
    assert not [e for e in log.events if e.type == "model_switch"]


def test_resume_continues_on_the_last_model(tmp_path, monkeypatch):
    log = Log.new(cwd=str(tmp_path), provider="openrouter", model="m1")
    log.append("model_switch", {"provider": "deepseek", "model": "deepseek-v4-pro"})
    args = argparse.Namespace(model=None)
    assert start_ref(args, Log.load(log.path), Router("openrouter")) == Ref("deepseek", "deepseek-v4-pro")
    args.model = "anthropic:claude-x"                                      # -m wins over the log
    assert start_ref(args, log, Router("openrouter")) == Ref("anthropic", "claude-x")
    assert start_ref(argparse.Namespace(model=None), None, Router("anthropic")) == Ref(
        "anthropic", "claude-sonnet-5-5")
    with pytest.raises(TarjumanError, match="-m"):
        start_ref(argparse.Namespace(model=None), None, Router("local"))


def test_catalog_lines():
    line = describe(Ref("anthropic", "claude-sonnet-5-5"))
    assert line.startswith("anthropic:claude-sonnet-5-5 · ") and "context" in line and "$" in line
    assert "not in the catalog" in describe(Ref("local", "my-model"))
    found = listing("deepseek v4", "openrouter")
    assert found and all(line.startswith("deepseek:") for line in found if not line.startswith("..."))
    assert "no catalog" in listing("local", "openrouter")[0]

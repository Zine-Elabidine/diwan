"""A message reaches a session whenever it comes: queued during a turn, waking it when idle,
starting the next turn when it lands just as one ends, kept for later when a turn is stopped."""

import pytest
from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.events import ToolStarted, TurnEnded
from diwan.log import Log
from diwan.runner import Busy, Runner
from diwan.tools import default_tools


def reply(text):
    return Message("assistant", [Text(text)])


def glob():
    return Message("assistant", [ToolCall("c1", "glob", '{"pattern": "*.py"}')])


def make(tmp_path, script, on=None):
    """A runner on a scripted agent; `on(runner, event)` sees the agent's events."""
    log = Log.new(cwd=str(tmp_path))
    agent = Agent(Fake(script), "fake-model", log, default_tools(), "sys", sleep=lambda s: None)
    woke, ended = [], []
    runner = Runner(agent, on_wake=woke.append, on_woken_end=ended.append)
    if on:
        agent.on = lambda e: on(runner, e)
    return runner, woke, ended


def texts(runner):
    return [(m.role, m.text) for m in runner.agent.log.messages() if m.role in ("user", "assistant")]


def settle(runner):
    while runner.thread is not None and runner.thread.is_alive():
        runner.thread.join(5)


def test_a_message_wakes_an_idle_session(tmp_path):
    runner, woke, ended = make(tmp_path, [reply("on it")])
    assert runner.deliver("session 2 freed api.py") is True
    settle(runner)
    assert woke == ["session 2 freed api.py"] and [e.reason for e in ended] == ["done"]
    assert texts(runner) == [("user", "session 2 freed api.py"), ("assistant", "on it")]
    assert not runner.busy


def test_during_a_turn_it_waits_for_the_next_request(tmp_path):
    answers = []

    def on(runner, e):
        if isinstance(e, ToolStarted):
            answers.append(runner.deliver("also check the tests"))

    runner, woke, _ = make(tmp_path, [glob(), reply("done")], on)
    assert runner.run("list the files").reason == "done"
    assert answers == [False] and woke == []                  # queued, no second turn
    assert ("user", "also check the tests") in texts(runner)


def test_a_message_as_the_turn_ends_starts_the_next(tmp_path):
    def on(runner, e):
        if isinstance(e, TurnEnded) and len(texts(runner)) == 2:   # the first turn's end
            runner.deliver("one more thing")

    runner, woke, ended = make(tmp_path, [reply("first"), reply("second")], on)
    runner.run("hello")
    settle(runner)
    assert woke == ["one more thing"] and [e.reason for e in ended] == ["done"]
    assert [t for _, t in texts(runner)] == ["hello", "first", "one more thing", "second"]


def test_messages_left_by_a_stopped_turn_lead_the_next_one(tmp_path):
    def on(runner, e):
        if isinstance(e, ToolStarted):
            runner.deliver("from session 2")
            runner.agent.interrupt()

    runner, woke, _ = make(tmp_path, [glob(), reply("never"), reply("ok")], on)
    assert runner.run("go").reason == "interrupted"
    assert woke == [] and not runner.busy                        # stopping doesn't restart it
    runner.agent.on = lambda e: None
    runner.run("continue")
    assert ("user", "from session 2\n\ncontinue") in texts(runner)


def test_one_turn_at_a_time(tmp_path):
    def on(runner, e):
        if isinstance(e, ToolStarted):
            with pytest.raises(Busy):
                runner.run("another prompt")

    runner, _, _ = make(tmp_path, [glob(), reply("done")], on)
    assert runner.run("go").reason == "done"

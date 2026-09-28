import json

import pytest

from tarjuman import Message, TarjumanError, Text, ToolCall, ToolResult, Usage
from tarjuman.fake import Fake

from diwan.agent import Agent, Limits, ToolFinished, TurnEnded
from diwan.log import Log
from diwan.tools import make_tools


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DIWAN_HOME", str(tmp_path / "home"))


def say(text, **kw):
    return Message("assistant", [Text(text)], **kw)


def call(name, cid="c1", **args):
    return Message("assistant", [ToolCall(cid, name, json.dumps(args))])


def agent(tmp_path, script, approve=lambda c, s: True, **kw):
    log = Log.new(cwd=str(tmp_path))
    events = []
    a = Agent(Fake(script), "fake-model", log, make_tools(tmp_path), "sys",
              approve=approve, on=events.append, sleep=lambda s: None, **kw)
    return a, log, events


def test_plain_answer_ends_the_turn(tmp_path):
    a, log, _ = agent(tmp_path, [say("hello")])
    ended = a.turn("hi")
    assert ended.reason == "done" and ended.steps == 1
    assert [m.role for m in log.messages()] == ["user", "assistant"]


def test_tool_loop_writes_and_reads_a_file(tmp_path):
    a, log, events = agent(tmp_path, [
        call("write", path="notes/a.txt", content="one\ntwo"),
        call("read", cid="c2", path="notes/a.txt"),
        say("done"),
    ])
    ended = a.turn("make a file")
    assert ended.reason == "done" and ended.steps == 3
    assert (tmp_path / "notes/a.txt").read_text() == "one\ntwo"
    results = [e.result for e in events if isinstance(e, ToolFinished)]
    assert "Created notes/a.txt" in results[0].content
    assert results[1].content == "     1\tone\n     2\ttwo"
    # the second request carried the first tool result
    sent = a.provider.requests[1]
    assert sent[0].role == "system" and sent[-1].role == "tool"


def test_denied_action_is_reported_to_the_model(tmp_path):
    a, log, _ = agent(tmp_path, [call("bash", command="rm -rf x"), say("ok, asking")],
                      approve=lambda c, s: False)
    a.turn("clean up")
    tool_msg = [m for m in log.messages() if m.role == "tool"][0]
    assert tool_msg.content[0].is_error and "denied" in tool_msg.content[0].content
    assert any(e.type == "approval" and e.data["allowed"] is False for e in log.events)


def test_read_only_tools_skip_approval(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    asked = []
    a, _, _ = agent(tmp_path, [call("read", path="f.txt"), say("ok")],
                    approve=lambda c, s: asked.append(c) or True)
    a.turn("read it")
    assert asked == []


def test_bad_tool_input_goes_back_as_an_error(tmp_path):
    a, log, _ = agent(tmp_path, [
        Message("assistant", [ToolCall("c1", "edit", "{not json")]),
        call("nope", cid="c2"),
        call("edit", cid="c3", path="missing.py", old="a", new="b"),
        say("gave up"),
    ])
    a.turn("x")
    errors = [b for m in log.messages() if m.role == "tool" for b in m.content]
    assert all(e.is_error for e in errors)
    assert "Invalid JSON" in errors[0].content
    assert "Unknown tool" in errors[1].content
    assert "no such file" in errors[2].content


def test_retries_then_succeeds_and_hides_failures_from_the_model(tmp_path):
    a, log, events = agent(tmp_path, [TarjumanError("RATE_LIMIT", "slow"), say("hi")])
    ended = a.turn("x")
    assert ended.reason == "done"
    assert [m.role for m in log.messages()] == ["user", "assistant"]
    assert [e.data["code"] for e in log.events if e.type == "error"] == ["RATE_LIMIT"]


def test_non_retryable_error_ends_the_turn(tmp_path):
    a, _, _ = agent(tmp_path, [TarjumanError("INVALID_CREDENTIAL", "bad key")])
    ended = a.turn("x")
    assert ended.reason == "error" and "bad key" in ended.error


def test_max_steps(tmp_path):
    a, _, _ = agent(tmp_path, [call("bash", cid=f"c{i}", command="true") for i in range(3)],
                    limits=Limits(max_steps=2))
    assert a.turn("loop").reason == "max_steps"


def test_max_tokens_ends_the_turn(tmp_path):
    a, _, _ = agent(tmp_path, [say("cut", stop="max_tokens")])
    assert a.turn("x").reason == "max_tokens"


def test_usage_is_summed_over_steps(tmp_path):
    u = Usage(10, 0, 0, 5, 0, 0.01)
    a, log, _ = agent(tmp_path, [
        Message("assistant", [ToolCall("c1", "bash", '{"command":"true"}')], usage=u),
        Message("assistant", [Text("ok")], usage=u)])
    ended = a.turn("x")
    assert (ended.usage.input, ended.usage.output) == (20, 10)
    assert ended.usage.cost == pytest.approx(0.02)
    assert log.events[-1].type == "turn_end" and log.events[-1].data["steps"] == 2


def test_log_survives_reload_and_resume(tmp_path):
    a, log, _ = agent(tmp_path, [say("first")])
    a.turn("one")
    again = Log.load(log.path)
    assert [m.text for m in again.messages()] == ["one", "first"]
    assert Log.latest(str(tmp_path)).path == log.path
    b = Agent(Fake([say("second")]), "m", again, make_tools(tmp_path), "sys")
    b.turn("two")
    assert [m.text for m in b.provider.requests[0][1:]] == ["one", "first", "two"]


def test_tree_branching_keeps_everything(tmp_path):
    a, log, _ = agent(tmp_path, [say("A"), say("B")])
    a.turn("q1")
    fork_point = log.head
    a.turn("q2")
    log.head = fork_point          # move back: the old branch stays in the file
    assert [m.text for m in log.messages()] == ["q1", "A"]
    assert len(Log.load(log.path).events) == len(log.events)


def test_edit_tool_rules(tmp_path):
    tools = make_tools(tmp_path)
    (tmp_path / "f.py").write_text("x = 1\nx = 1\n")
    from diwan.tools import ToolError
    with pytest.raises(ToolError, match="2 times"):
        tools["edit"].run(path="f.py", old="x = 1", new="x = 2")
    tools["edit"].run(path="f.py", old="x = 1", new="x = 2", replace_all=True)
    assert (tmp_path / "f.py").read_text() == "x = 2\nx = 2\n"


def test_bash_reports_exit_code_and_stderr(tmp_path):
    out = make_tools(tmp_path)["bash"].run(command="echo hi; echo oops >&2; exit 3")
    assert "hi" in out and "oops" in out and "[exit code 3]" in out

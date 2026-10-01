
import pytest
from tarjuman import (Finish, Message, Reasoning, Replay, TarjumanError, Text, TextDelta, ToolCall,
                      Usage)
from tarjuman.fake import Fake

from diwan.agent import INTERRUPTED, Agent, Limits, ToolFinished
from diwan.log import Log
from diwan.tools import make_tools
from helpers import call, say


def agent(tmp_path, script, approve=lambda c, s, outside: True, **kw):
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
    a, _log, events = agent(tmp_path, [
        call("write", path="notes/a.txt", content="one\ntwo"),
        call("read", cid="c2", path="notes/a.txt"),
        say("done"),
    ])
    ended = a.turn("make a file")
    assert ended.reason == "done" and ended.steps == 3
    assert (tmp_path / "notes/a.txt").read_text() == "one\ntwo"
    results = [e.result for e in events if isinstance(e, ToolFinished)]
    assert "Created notes/a.txt" in results[0].text
    assert results[1].text == "     1\tone\n     2\ttwo"
    # the second request carried the first tool result
    sent = a.provider.requests[1]
    assert sent[0].role == "system" and sent[-1].role == "tool"


def test_denied_action_is_reported_to_the_model(tmp_path):
    a, log, _ = agent(tmp_path, [call("bash", command="rm -rf x"), say("ok, asking")],
                      approve=lambda c, s, outside: False)
    a.turn("clean up")
    tool_msg = next(m for m in log.messages() if m.role == "tool")
    assert tool_msg.content[0].is_error and "denied" in tool_msg.content[0].text
    assert any(e.type == "approval" and e.data["allowed"] is False for e in log.events)


def test_read_only_tools_skip_approval(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    asked = []
    a, _, _ = agent(tmp_path, [call("read", path="f.txt"), say("ok")],
                    approve=lambda c, s, outside: asked.append(c) or True)
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
    assert "Invalid JSON" in errors[0].text
    assert "Unknown tool" in errors[1].text
    assert "no such file" in errors[2].text


def test_retries_then_succeeds_and_hides_failures_from_the_model(tmp_path):
    a, log, _events = agent(tmp_path, [TarjumanError("RATE_LIMIT", "slow"), say("hi")])
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


def test_ui_renders_markdown_tools_and_small_costs(tmp_path):
    from rich.console import Console

    from diwan.ui import Terminal, fmt_cost

    assert fmt_cost(0.0000694) == "$0.000069" and fmt_cost(0) == "$0" and fmt_cost(0.25) == "$0.2500"
    console = Console(record=True, width=80, force_terminal=False)
    term = Terminal(console, auto_approve=True)
    a = Agent(Fake([Message("assistant", [Text("thinking"), ToolCall("c1", "bash", '{"command":"echo hi"}')]),
                    Message("assistant", [Text("My name is **Diwan**.")],
                            usage=Usage(900, 0, 0, 70, 0, 0.0000694))]),
              "m", Log.new(cwd=str(tmp_path)), make_tools(tmp_path), "sys",
              approve=term.approve, on=term.on)
    a.turn("hi")
    out = console.export_text()
    assert "**" not in out and "My name is Diwan." in out
    assert "● bash  echo hi" in out and "⎿ hi" in out
    assert "$0.000069" in out and "session $0.000069" in out


def test_interrupt_before_anything_streamed_keeps_only_the_marker(tmp_path):
    a, log, _ = agent(tmp_path, [say("a long answer")])
    a.on = lambda ev: a.interrupt()          # stop as soon as anything happens
    assert a.turn("x").reason == "interrupted"
    assert [m.role for m in log.messages()] == ["user", "system"]
    assert log.messages()[1].text == INTERRUPTED


def test_interrupt_after_the_answer_means_the_tool_never_ran(tmp_path):
    b, log, _ = agent(tmp_path, [call("bash", command="true"), say("never")])
    b.on = lambda ev: b.interrupt() if isinstance(ev, Finish) else None
    assert b.turn("x").reason == "interrupted"
    assert not [m for m in log.messages() if m.role == "tool"]


def test_interrupt_mid_stream_keeps_finished_blocks_signed_and_the_cut_one_apart(tmp_path):
    answer = Message("assistant", [Reasoning("plan"), Text("Let me look at the tests"),
                                   ToolCall("c1", "bash", '{"command": "pytest"}')],
                     replay=Replay(None, ["sig-1", None, None]))
    fake = Fake([answer, say("ok, the README")])
    log = Log.new(cwd=str(tmp_path))
    a = Agent(fake, "fake-model", log, make_tools(tmp_path), "sys", sleep=lambda s: None)
    a.on = lambda ev: a.interrupt() if isinstance(ev, TextDelta) else None
    assert a.turn("fix it").reason == "interrupted"

    cut = log.messages()[1]
    assert cut.stop == "interrupted"
    assert cut.content == [Reasoning("plan")] and cut.replay.blocks == ["sig-1"]   # finished, signed
    assert cut.partial == [Text("Let me look at the tests")]                        # kept, never sent

    a.on = lambda ev: None
    a.turn("no, check the README")
    sent = fake.requests[-1]
    assert [m.role for m in sent] == ["system", "user", "assistant", "user", "user"]
    assert sent[2].content == [Reasoning("plan")] and sent[2].replay.blocks == ["sig-1"]
    assert INTERRUPTED in sent[3].text and "<system-reminder>" in sent[3].text
    assert sent[4].text == "no, check the README"


def test_an_unexpected_error_ends_the_turn_not_the_session(tmp_path):
    class Broken(Fake):
        def stream(self, *a, **kw):
            raise RuntimeError("provider bug")

    log = Log.new(cwd=str(tmp_path))
    a = Agent(Broken([]), "fake-model", log, make_tools(tmp_path), "sys")
    ended = a.turn("hi")
    assert ended.reason == "error" and "RuntimeError: provider bug" in ended.error
    err = next(e for e in log.events if e.type == "error")
    assert err.data["code"] == "INTERNAL" and "Traceback" in err.data["traceback"]
    a.provider = Fake([say("still here")])
    assert a.turn("again").reason == "done"

"""The `agent` tool: a fresh child agent does a task and the parent gets only its answer.
Parent and child share the scripted provider, so the script interleaves their replies."""

import json

from tarjuman import Message, Text, ToolCall, Usage
from tarjuman.fake import Fake

from diwan import prompt
from diwan.agent import Agent
from diwan.events import ChildEvent, ToolStarted
from diwan.log import Log, sessions_dir
from diwan.tools import default_tools


def call(id, name, **args):
    return Message("assistant", [ToolCall(id, name, json.dumps(args))])


def reply(text, cost=None):
    return Message("assistant", [Text(text)], usage=Usage(10, 0, 0, 5, 0, cost))


def run(tmp_path, script, on=None):
    log = Log.new(cwd=str(tmp_path))
    events = []
    a = Agent(Fake(script), "fake-model", log, default_tools(), "sys", sleep=lambda s: None)
    a.on = on(a, events) if on else events.append
    return a, a.turn("where is the retry logic?"), events


def test_the_parent_gets_only_the_childs_answer(tmp_path):
    (tmp_path / "retry.py").write_text("def with_retry(): ...\n")
    script = [call("p1", "agent", task="find the retry logic", readonly=True),
              call("k1", "glob", pattern="*.py"),                    # the child works...
              reply("with_retry() in retry.py:1", cost=0.002),       # ...and answers
              reply("It's in retry.py:1.")]                          # the parent's answer
    a, ended, events = run(tmp_path, script)
    assert ended.reason == "done"
    result = a.log.messages()[2].tool_results[0].text
    assert result.startswith("with_retry() in retry.py:1") and "[agent: 2 steps;" in result
    assert "glob" not in [c.name for m in a.log.messages() for c in m.tool_calls]   # not the parent's
    child_request = a.provider.requests[1]
    assert child_request[0].text.endswith(prompt.CHILD) and child_request[1].text == "find the retry logic"
    assert a.total.cost is not None and a.total.cost >= 0.002          # the child's cost counts
    assert ended.usage.cost is not None and ended.usage.cost >= 0.002   # in the turn's too
    assert any(isinstance(e, ChildEvent) and isinstance(e.event, ToolStarted) for e in events)
    child = next(lg for lg in map(Log.load, sessions_dir().glob("*.jsonl"))
                 if lg.events[0].data.get("task") == "find the retry logic")
    assert child.events[0].data["parent"] == a.log.events[0].data["id"]


def test_a_readonly_child_cannot_write_or_start_agents(tmp_path):
    script = [call("p1", "agent", task="look around", readonly=True),
              call("k1", "write", path="x.txt", content="hi"),
              call("k2", "agent", task="deeper", readonly=True),
              reply("I could not write."),
              reply("ok")]
    a, ended, _ = run(tmp_path, script)
    assert ended.reason == "done" and not (tmp_path / "x.txt").exists()
    results = [r.text for m in a.provider.requests[3] for r in m.tool_results]   # the child's last request
    assert any("write" in t and "unknown" in t.lower() for t in results)
    assert any("agent" in t and "unknown" in t.lower() for t in results)


def test_stopping_the_parent_stops_the_child(tmp_path):
    def on(a, events):
        def handle(e):
            events.append(e)
            if isinstance(e, ChildEvent) and isinstance(e.event, ToolStarted):
                a.interrupt()
        return handle

    script = [call("p1", "agent", task="run the tests", readonly=False),
              call("k1", "glob", pattern="*"), call("k2", "glob", pattern="*"), reply("never")]
    a, ended, _ = run(tmp_path, script, on)
    assert ended.reason == "interrupted"
    assert len(a.provider.requests) == 2          # the child's second step never ran


def test_resuming_picks_the_users_session_not_a_childs(tmp_path):
    script = [call("p1", "agent", task="look", readonly=True), reply("seen"), reply("ok")]
    a, _, _ = run(tmp_path, script)
    assert Log.latest(str(tmp_path)).id == a.log.id


def test_a_fork_starts_from_the_parents_last_request(tmp_path):
    script = [call("p1", "agent", task="try the other approach", readonly=True, fork=True),
              call("k1", "write", path="x.txt", content="hi"),     # refused: read-only fork
              call("k2", "agent", task="deeper", readonly=True),   # refused: depth 1
              reply("the other approach is worse"),
              reply("ok")]
    a, ended, _ = run(tmp_path, script)
    assert ended.reason == "done" and not (tmp_path / "x.txt").exists()
    parent, child = a.provider.requests[0], a.provider.requests[1]
    assert child[:len(parent)] == parent                       # same system prompt and messages
    assert "Task: try the other approach" in child[len(parent)].text
    assert prompt.CHILD not in child[0].text                   # the parent's prompt, unchanged
    refused = [r.text for m in a.provider.requests[3] for r in m.tool_results]
    assert all("Unknown tool" in t for t in refused) and len(refused) == 2
    assert "[agent: 3 steps;" in a.log.messages()[2].tool_results[0].text

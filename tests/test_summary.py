"""The summary tier, step (a): a "summary" event in the log replaces the oldest messages in
every later request. Events are written by hand here; making a summary comes later."""

from tarjuman import Message, Text, ToolCall, ToolResult, Usage
from tarjuman.fake import Fake

from diwan.context import ContextManager
from diwan.log import Log
from test_clearing import history


def session(tmp_path):
    """user, call, result, reply, user: five messages, indexes 0 to 4."""
    log = Log.new(cwd=str(tmp_path))
    log.add_message(Message.user("fix the tests"))
    log.add_message(Message("assistant", [ToolCall("c1", "read", '{"path": "calc.py"}')]))
    log.add_message(Message("tool", [ToolResult("c1", "def add(a, b): ...")]))
    log.add_message(Message("assistant", [Text("add() is wrong")]))
    log.add_message(Message.user("fix it"))
    return log


def texts(messages):
    return [(m.role, m.text) for m in messages]


def test_the_summary_replaces_the_first_cut_messages(tmp_path):
    log = session(tmp_path)
    log.append("summary", {"cut": 3, "text": "Goal: fix the tests."})
    view = ContextManager(log, answer_room=1_000).view()
    assert texts(view) == [("user", "Goal: fix the tests."),        # messages 0-2 replaced
                           ("assistant", "add() is wrong"),         # message 3 onward as before
                           ("user", "fix it")]
    assert len(log.messages()) == 5                                 # the log still has them all


def test_a_resumed_session_rebuilds_the_same_view(tmp_path):
    log = session(tmp_path)
    log.append("summary", {"cut": 3, "text": "Goal: fix the tests."})
    before = ContextManager(log, answer_room=1_000).view()
    after = ContextManager(Log.load(log.path), answer_room=1_000).view()
    assert texts(after) == texts(before)


def test_only_the_latest_summary_counts(tmp_path):
    log = session(tmp_path)
    log.append("summary", {"cut": 1, "text": "first"})
    log.append("summary", {"cut": 3, "text": "second, covering the first"})
    view = ContextManager(log, answer_room=1_000).view()
    assert texts(view)[0] == ("user", "second, covering the first") and len(view) == 3


def test_a_branch_from_before_the_summary_never_sees_it(tmp_path):
    log = session(tmp_path)
    fork = log.head
    log.append("summary", {"cut": 3, "text": "summary"})
    log.head = fork
    log.add_message(Message("assistant", [Text("another way")]))
    assert log.summary() is None
    assert len(ContextManager(log, answer_room=1_000).view()) == 6


def test_masks_still_apply_after_the_cut(tmp_path):
    log = session(tmp_path)
    log.add_message(Message("assistant", [ToolCall("c2", "read", '{"path": "big.py"}')]))
    log.add_message(Message("tool", [ToolResult("c2", "x" * 5_000)]))
    log.append("mask", {"entries": {"out:c2": "[cleared]"}})
    log.append("summary", {"cut": 3, "text": "summary"})
    view = ContextManager(log, answer_room=1_000).view()
    assert view[-1].tool_results[0].text == "[cleared]"


def test_a_report_from_before_the_summary_is_not_trusted(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    log.add_message(Message.user("x" * 90_000))
    log.add_message(Message("assistant", [Text("read it")], "fake", "fake-model",
                            Usage(input=30_000, output=10), "end"))
    log.add_message(Message.user("next"))
    log.add_message(Message("assistant", [Text("ok")], "fake", "fake-model",
                            Usage(input=30_100, output=10), "end"))
    cm = ContextManager(log, answer_room=1_000)
    provider = Fake([], window=100_000)
    assert cm.measure("sys", [], provider, "fake-model").used == 30_110
    log.append("summary", {"cut": 2, "text": "The user sent a long text; I read it."})
    use = cm.measure("sys", [], provider, "fake-model")
    # message 3's report counted the 90k characters: neither its count nor its ratio is used
    assert use.used < 100 and not use.exact

    log.add_message(Message("assistant", [Text("done")], "fake", "fake-model",
                            Usage(input=60, output=5), "end"))
    use = cm.measure("sys", [], provider, "fake-model")
    assert use.exact and use.used == 65                             # a report after it counts


def test_clearing_ignores_summarized_messages(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    for m in history(16):                                           # ~21k tokens, all old
        log.add_message(m)
    cm = ContextManager(log, answer_room=4_000)
    provider = Fake([], window=40_000)
    use = cm.measure("sys", [], provider, "fake-model")
    log.append("summary", {"cut": len(log.messages()) - 1, "text": "read 16 files"})
    assert cm.clear(use) is None                  # the big outputs are no longer sent
    assert log.events[-1].type == "summary"       # so no mask event was logged

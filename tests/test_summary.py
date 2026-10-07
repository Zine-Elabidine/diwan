"""The summary tier, step (a): a "summary" event in the log replaces the oldest messages in
every later request. Events are written by hand here; making a summary comes later."""

from tarjuman import Message, Reasoning, Text, ToolCall, ToolResult, Usage
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


# --- step (b1): where to cut, what the summarizer is sent, the stored text ----------------------

from diwan import summary  # noqa: E402


def seven():
    """The explainer's example: two read/test steps, then a reply and a user message."""
    return [Message.user("fix the tests"),
            Message("assistant", [ToolCall("c1", "read", '{"path": "calc.py"}')]),
            Message("tool", [ToolResult("c1", "def add(a, b): ...")]),
            Message("assistant", [ToolCall("c2", "bash", '{"command": "pytest"}')]),
            Message("tool", [ToolResult("c2", "1 failed: test_add")]),
            Message("assistant", [Text("add() is wrong, I will fix it")]),
            Message.user("go ahead")]


def test_a_cut_never_lands_on_a_tool_result():
    msgs = seven()
    assert [c for c in range(9) if summary.valid_cut(msgs, c)] == [1, 3, 5, 6, 7]


def test_the_trigger_and_target_follow_the_effective_window():
    assert summary.effective(36_000) == 36_000
    assert summary.effective(1_000_000) == 266_666          # the 75% trigger lands at 200k
    assert not summary.due(199_000, 1_000_000) and summary.due(200_000, 1_000_000)
    assert not summary.due(26_000, 36_000) and summary.due(27_000, 36_000)
    assert 0 < summary.tail_budget(36_000) < 0.40 * 36_000


def test_the_cut_keeps_as_much_recent_text_as_fits_and_prefers_an_assistant_message():
    msgs = seven()
    everything = summary.choose_cut(msgs, {}, 0, budget=10_000, chars_per_token=3)
    assert everything == 1                                  # all of it fits: cut as little as possible
    small = summary.choose_cut(msgs, {}, 0, budget=40, chars_per_token=3)
    assert small is not None and msgs[small].role == "assistant"


def test_the_models_latest_message_and_what_follows_are_never_summarized():
    msgs = seven()[:5]                                      # mid-turn: ends on the pytest call and result
    assert summary.choose_cut(msgs, {}, 0, budget=0, chars_per_token=3) == 3
    assert summary.choose_cut(seven(), {}, 0, budget=0, chars_per_token=3) == 5


def test_the_cut_moves_forward_or_not_at_all():
    msgs = seven()
    assert summary.choose_cut(msgs, {}, 3, budget=0, chars_per_token=3) == 5
    assert summary.choose_cut(msgs, {}, 5, budget=0, chars_per_token=3) is None


def test_the_transcript_is_plain_text_as_the_model_saw_it():
    msgs = seven()
    msgs[1] = Message("assistant", [Reasoning("thinking hard"),
                                    ToolCall("c1", "read", '{"path": "calc.py"}')])
    msgs[2] = Message("tool", [ToolResult("c1", "x" * 5_000)])
    t = summary.transcript(msgs, {"out:c2": "[cleared]"}, item_chars=300)
    assert "thinking hard" not in t                          # no reasoning
    assert '[call read] {"path": "calc.py"}' in t
    assert "[read result] " in t and "characters cut" in t   # long output cut in the middle
    assert "[bash result] [cleared]" in t                    # masks applied
    assert "[user] go ahead" in t


def test_the_summarizer_request_has_no_tools_and_updates_the_previous_note():
    first = summary.request("m", None, "T", max_tokens=1_000)
    assert first.tools is None and first.reasoning == "off" and first.max_tokens == 1_000
    assert "<note>" not in first.messages[0].text
    again = summary.request("m", "## Goal\nfix the tests", "T", max_tokens=1_000)
    assert "<note>\n## Goal\nfix the tests\n</note>" in again.messages[0].text


def test_the_stored_text_copies_the_users_words_notes_and_changed_files():
    msgs = [Message.user("fix the tests, no new dependencies"),
            Message("assistant", [ToolCall("n1", "note", '{"kind": "decision", "text": "keep stdlib"}'),
                                  ToolCall("e1", "edit", '{"path": "calc.py", "old": "-", "new": "+"}'),
                                  ToolCall("e2", "write", '{"path": "nope.py", "content": "x"}')]),
            Message("tool", [ToolResult("n1", "noted"), ToolResult("e1", "edited"),
                             ToolResult("e2", "denied", True)])]
    text = summary.final_text("## Goal\nfix the tests", msgs, usable=36_000, chars_per_token=3)
    assert text.startswith(summary.HEADER)
    assert "## Goal\nfix the tests" in text
    assert "- fix the tests, no new dependencies" in text
    assert "- decision: keep stdlib" in text
    assert "- calc.py" in text and "nope.py" not in text     # failed writes don't count


def test_only_the_newest_user_messages_are_kept_when_they_dont_fit():
    msgs = [Message.user(f"message {i} " + "x" * 280) for i in range(10)]
    kept = summary.user_messages(msgs, chars=900)
    assert len(kept) == 3 and kept[-1].startswith("message 9")


def test_the_next_summary_updates_the_models_part_only(tmp_path):
    log = session(tmp_path)
    log.append("summary", {"cut": 3, "text": "HEADER + note + user messages", "model_text": "note"})
    assert log.summary().model_text == "note"
    log.append("summary", {"cut": 4, "text": "older event without model_text"})
    assert log.summary().model_text == "older event without model_text"

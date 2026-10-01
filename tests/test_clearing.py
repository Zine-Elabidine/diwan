"""Clearing old tool outputs (clearing.py): what is cleared and kept, in batches, recorded in
the log so later requests are identical, and the gauge drops right away."""

import json

from tarjuman import Message, Text, ToolCall, ToolResult, Usage
from tarjuman.fake import Fake

from diwan import clearing
from diwan.agent import Agent, Limits
from diwan.events import ContextCleared
from diwan.log import Log
from diwan.tools import default_tools


def history(n, size=4_000):
    """A user message, then n read steps returning `size` characters each."""
    msgs = [Message.user("look at the files")]
    for i in range(n):
        msgs.append(Message("assistant", [ToolCall(f"c{i}", "read", json.dumps({"path": f"f{i}.py"}))]))
        msgs.append(Message("tool", [ToolResult(f"c{i}", "line\n" * (size // 5))]))
    return msgs


def test_old_outputs_are_cleared_newest_and_last_step_kept():
    msgs = history(10)                        # 10 outputs of ~1,000 tokens at 4 chars/token
    p = clearing.plan(msgs, {}, usable=10_000, chars_per_token=4)   # keep the newest 2,500 tokens
    assert p is not None
    assert set(p.entries) == {f"out:c{i}" for i in range(8)}  # c8 and c9 kept
    view = clearing.apply(msgs, p.entries)
    assert view[2].content[0].text.startswith("[Output cleared to save context: read f0.py, 801 lines")
    assert view[-1].content[0].text == msgs[-1].content[0].text
    assert view[1] == msgs[1] and view[0] == msgs[0]           # calls and messages untouched


def test_small_savings_wait_for_a_bigger_batch():
    assert clearing.plan(history(3, size=400), {}, usable=10_000, chars_per_token=4) is None


def test_already_cleared_outputs_are_not_cleared_again():
    msgs = history(10)
    first = clearing.plan(msgs, {}, usable=10_000, chars_per_token=4)
    assert clearing.plan(msgs, first.entries, usable=10_000, chars_per_token=4) is None


def test_big_write_contents_are_cleared_but_stay_valid_json():
    msgs = [Message.user("write it"),
            Message("assistant", [ToolCall("w", "write",
                                           json.dumps({"path": "a.py", "content": "x" * 50_000}))]),
            Message("tool", [ToolResult("w", "Wrote a.py")]),
            *history(6)[1:]]
    p = clearing.plan(msgs, {}, usable=10_000, chars_per_token=4)
    args = json.loads(p.entries["args:w"])
    assert args == {"path": "a.py", "content": "[50,000 characters cleared to save context]"}
    view = clearing.apply(msgs, p.entries)
    assert view[1].tool_calls[0].args()["path"] == "a.py"


def test_the_agent_clears_at_half_full_records_it_and_the_gauge_drops(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    for m in history(12)[1:]:
        log.add_message(m) if m.role != "user" else None
    log.add_message(Message("assistant", [Text("done reading")], "fake", "fake-model",
                            Usage(input=11_000, output=10), "end"))
    events = []
    a = Agent(Fake([Message("assistant", [Text("ok")])], window=36_000), "fake-model", log,
              default_tools(), "sys", on=events.append, limits=Limits(max_tokens=16_000),
              sleep=lambda s: None)
    before = a.context_use
    assert before.fraction > 0.5                                # 11k of 20k usable
    a.turn("next")
    cleared = [e for e in events if isinstance(e, ContextCleared)]
    assert cleared and cleared[0].text.startswith("cleared ")
    assert any(e.type == "mask" for e in log.path_to_head())
    assert log.masked()                                         # later requests rebuild the same view
    sent = a.provider.requests[-1]
    assert any("[Output cleared" in m.content[0].text for m in sent if m.role == "tool")


def test_the_context_manager_works_without_an_agent(tmp_path):
    from diwan.context import ContextManager

    log = Log.new(cwd=str(tmp_path))
    for m in history(16):   # ~21k tokens of a 36k usable window
        log.add_message(m)
    cm = ContextManager(log, answer_room=4_000)
    use = cm.measure("sys", [], Fake([], window=40_000), "fake-model")
    assert use.usable == 36_000 and not use.exact and use.fraction > clearing.MASK_AT
    assert use.chars_per_token == 3.0                # no report yet: the default ratio
    p = cm.clear(use)
    assert p is not None and log.events[-1].type == "mask"
    assert cm.measure("sys", [], Fake([], window=40_000), "fake-model").used < use.used
    assert cm.clear(use) is None                     # nothing left worth clearing

"""Neighbouring calls that need no approval and change nothing run at the same time; the
rest keep their turn, and results stay in the calls' order."""

import json
import threading

from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.tools import Tool, ToolContext, default_tools
from diwan.tools.base import schema


class Meet(Tool):
    """Read-only; returns only once `parties` calls are inside it at the same time."""
    name, description, readonly = "meet", "wait for the others", True
    parameters = schema({"tag": {"type": "string"}}, ["tag"])

    def __init__(self, parties):
        self.barrier = threading.Barrier(parties, timeout=2)

    def run(self, ctx: ToolContext, tag: str) -> str:
        self.barrier.wait()        # raises BrokenBarrierError if the calls ran one by one
        return f"met {tag}"


def calls(*specs):
    return Message("assistant", [ToolCall(f"c{i}", name, json.dumps(args))
                                 for i, (name, args) in enumerate(specs)])


def run(tmp_path, tools, msg, approve=lambda c, t, o: True):
    log = Log.new(cwd=str(tmp_path))
    a = Agent(Fake([msg, Message("assistant", [Text("done")])]), "m", log, tools, "sys",
              approve=approve)
    a.turn("go")
    return [r.text for r in log.messages()[2].tool_results]


def test_read_only_calls_run_together_and_keep_their_order(tmp_path):
    tools = {**default_tools(), "meet": Meet(3)}
    out = run(tmp_path, tools, calls(("meet", {"tag": "a"}), ("meet", {"tag": "b"}),
                                     ("meet", {"tag": "c"})))
    assert out == ["met a", "met b", "met c"]


def test_a_call_needing_approval_waits_its_turn(tmp_path):
    (tmp_path / "x.txt").write_text("x")
    asked = []
    tools = {**default_tools(), "meet": Meet(2)}
    out = run(tmp_path, tools,
              calls(("meet", {"tag": "a"}), ("meet", {"tag": "b"}),
                    ("write", {"path": "y.txt", "content": "y"}), ("read", {"path": "x.txt"})),
              approve=lambda c, t, o: asked.append(c.name) or True)
    assert out[:2] == ["met a", "met b"] and asked == ["write"]
    assert (tmp_path / "y.txt").read_text() == "y" and "x" in out[3]

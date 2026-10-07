"""recall (step c): the model searches the full log, including what clearing and summaries took
out of its context, and never its own earlier searches."""

import json

import pytest
from tarjuman import Message, Text, ToolCall, ToolResult
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.paths import PathPolicy
from diwan.tools import ToolContext, ToolError, default_tools

RECALL = default_tools()["recall"]


def session(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    log.add_message(Message.user("why do the tests fail?"))
    log.add_message(Message("assistant", [ToolCall("c1", "read", '{"path": "errors.py"}')]))
    log.add_message(Message("tool", [ToolResult("c1", "import x\nRETRYABLE = {RATE_LIMIT, NETWORK}\n")]))
    log.add_message(Message("assistant", [Text("Two codes are retryable.")]))
    return log


def recall(log, query, tmp_path):
    return RECALL.run(ToolContext(PathPolicy(tmp_path), log=log), query=query)


def test_finds_an_output_that_was_cleared_and_summarized(tmp_path):
    log = session(tmp_path)
    log.append("mask", {"entries": {"out:c1": "[cleared]"}})
    log.append("summary", {"cut": 3, "text": "read errors.py"})
    out = recall(log, "retryable", tmp_path)
    assert "[message 2, read errors.py output, line 2] RETRYABLE = {RATE_LIMIT, NETWORK}" in out
    assert "[message 3, your reply, line 1] Two codes are retryable." in out


def test_skips_its_own_earlier_searches(tmp_path):
    log = session(tmp_path)
    log.add_message(Message("assistant", [ToolCall("r1", "recall", '{"query": "RETRYABLE"}')]))
    log.add_message(Message("tool", [ToolResult("r1", "[message 2, ...] RETRYABLE = {...}")]))
    out = recall(log, "retryable", tmp_path)
    assert "message 4" not in out and "message 5" not in out


def test_no_match_and_bad_queries(tmp_path):
    log = session(tmp_path)
    assert recall(log, "OVERLOADED", tmp_path) == "No match for 'OVERLOADED' in this session."
    with pytest.raises(ToolError):
        recall(log, "  ", tmp_path)
    with pytest.raises(ToolError):
        RECALL.run(ToolContext(PathPolicy(tmp_path)), query="x")


def test_many_matches_keep_the_newest(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    log.add_message(Message.user("\n".join(f"hit {i}" for i in range(50))))
    out = recall(log, "hit", tmp_path)
    assert "line 50] hit 49" in out and "line 1] hit 0" not in out
    assert out.endswith("(30 older matches not shown: use a more specific query)")


def test_the_agent_gives_recall_the_session(tmp_path):
    log = session(tmp_path)
    script = [Message("assistant", [ToolCall("r1", "recall", json.dumps({"query": "RATE_LIMIT"}))]),
              Message("assistant", [Text("RATE_LIMIT and NETWORK.")])]
    a = Agent(Fake(script), "fake-model", log, default_tools(), "sys", sleep=lambda s: None)
    a.turn("which codes are retryable? don't read again")
    result = log.messages()[-2].tool_results[0]
    assert not result.is_error and "RETRYABLE = {RATE_LIMIT, NETWORK}" in result.text

"""Typing while the agent works: the message waits for the end of the current step, then the
model gets it; a turn about to end answers it first; what comes too late starts the next turn."""

from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.events import ToolStarted, UserAdded
from diwan.log import Log
from diwan.tools import default_tools


def reply(text):
    return Message("assistant", [Text(text)])


def call(i):
    return Message("assistant", [ToolCall(f"c{i}", "glob", '{"pattern": "*.py"}')])


def agent(tmp_path, script, on):
    log = Log.new(cwd=str(tmp_path))
    a = Agent(Fake(script), "fake-model", log, default_tools(), "sys", sleep=lambda s: None)
    a.on = lambda e: on(a, e)
    return a


def test_a_message_sent_during_a_tool_comes_after_its_result(tmp_path):
    seen = []

    def on(a, e):
        if isinstance(e, ToolStarted):
            a.send("also check the tests")
        if isinstance(e, UserAdded):
            seen.append(e.text)

    a = agent(tmp_path, [call(1), reply("done")], on)
    assert a.turn("list the files").reason == "done"
    roles = [(m.role, m.text) for m in a.log.messages()]
    assert roles[2][0] == "tool" and roles[3] == ("user", "also check the tests")
    assert a.provider.requests[1][-1].text.endswith("also check the tests")   # sent with step 2
    assert seen == ["also check the tests"]


def test_a_turn_about_to_end_answers_the_late_message(tmp_path):
    sent = []

    def on(a, e):
        if getattr(e, "text", None) == "first answer" and not sent:   # while the answer streams
            a.send("one more thing")
            sent.append(1)

    a = agent(tmp_path, [reply("first answer"), reply("second answer")], on)
    ended = a.turn("hello")
    assert ended.reason == "done" and ended.steps == 2
    assert [m.text for m in a.log.messages()] == ["hello", "first answer", "one more thing",
                                                  "second answer"]


def test_what_is_left_after_a_turn_is_handed_back(tmp_path):
    a = agent(tmp_path, [], lambda a, e: None)
    a.send("later")
    assert a.take_inbox() == ["later"] and a.take_inbox() == []

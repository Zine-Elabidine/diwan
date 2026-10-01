"""Esc must stop everything at once: the model stream, a running command and all it started,
a retry wait. Nothing produced so far is lost."""

import os
import sys
import threading
import time

import pytest
from tarjuman import Cancel, Message, TarjumanError
from tarjuman.fake import Fake
from helpers import call, say, tool

from diwan.agent import INTERRUPTED, Agent
from diwan.events import Retrying, ToolStarted
from diwan.log import Log
from diwan.tools import Read, ToolError, default_tools

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def later(seconds, fn):
    threading.Timer(seconds, fn).start()


@posix
def test_aborted_command_kills_its_children_and_keeps_the_output(tmp_path):
    c = Cancel()
    bash = tool("bash", tmp_path, c)
    later(0.3, c.cancel)
    start = time.monotonic()
    with pytest.raises(ToolError) as e:
        bash(command="sleep 30 & echo $! > child.pid; echo started; wait")
    assert time.monotonic() - start < 2
    assert str(e.value).startswith("aborted by user after") and "started" in str(e.value)
    child = int((tmp_path / "child.pid").read_text())
    time.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


@posix
def test_timeout_also_kills_the_tree(tmp_path):
    with pytest.raises(ToolError) as e:
        tool("bash", tmp_path)(command="echo hi; sleep 30", timeout=0.3)
    assert "timed out after" in str(e.value) and "hi" in str(e.value)


def test_commands_get_no_stdin(tmp_path):
    out = tool("bash", tmp_path)(command="cat; echo done")
    assert out.strip().endswith("done")


@posix
def test_esc_during_a_command_ends_the_turn_and_the_model_sees_why(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    a = Agent(Fake([call("bash", command="echo working; sleep 30")]), "fake-model", log,
              default_tools(), "sys")
    a.on = lambda ev: later(0.3, a.interrupt) if isinstance(ev, ToolStarted) else None
    start = time.monotonic()
    assert a.turn("run it").reason == "interrupted"
    assert time.monotonic() - start < 2
    tool = next(m for m in log.messages() if m.role == "tool").content[0]
    assert tool.is_error and "aborted by user" in tool.text and "working" in tool.text
    assert log.messages()[-1] == Message.system(INTERRUPTED)


def test_esc_during_a_retry_wait_returns_at_once(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    a = Agent(Fake([TarjumanError("RATE_LIMIT", "slow down", retry_after=30), say("never")]),
              "fake-model", log, default_tools(), "sys")
    a.on = lambda ev: later(0.1, a.interrupt) if isinstance(ev, Retrying) else None
    start = time.monotonic()
    assert a.turn("hi").reason == "interrupted"
    assert time.monotonic() - start < 1


def test_the_next_turn_starts_with_a_fresh_signal(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    a = Agent(Fake([say("one"), say("two")]), "fake-model", log, default_tools(), "sys")
    a.interrupt()                       # a stale Esc between turns
    assert a.turn("hi").reason == "done"


def test_a_real_ctrl_c_in_a_tool_ends_the_turn_as_interrupted(tmp_path):
    class Stubborn(Read):
        def run(self, ctx, **_):
            raise KeyboardInterrupt      # plain mode: SIGINT lands in whatever is running

    tools = default_tools()
    tools["read"] = Stubborn()
    log = Log.new(cwd=str(tmp_path))
    a = Agent(Fake([call("read", path="x")]), "fake-model", log, tools, "sys")
    assert a.turn("go").reason == "interrupted"
    assert log.messages()[-1] == Message.system(INTERRUPTED)


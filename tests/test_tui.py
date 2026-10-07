import json

import pytest
from tarjuman import Message, Text, ToolCall, Usage
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.models import Ref, Router
from diwan.tools import default_tools
from diwan.tui import Approval, DiwanApp, ToolView, UserMsg


def make_app(tmp_path, script):
    def make_agent(log, ref):
        return Agent(Fake(script), ref.model, log, default_tools(), "sys")
    return DiwanApp(make_agent, Log.new(cwd=str(tmp_path)), tmp_path, Router("openrouter"),
                    Ref("openrouter", "deepseek/deepseek-v4-flash"))


async def wait_idle(pilot, app):
    for _ in range(100):
        await pilot.pause(0.05)
        if not app.running:
            return
    raise AssertionError("agent never finished")


@pytest.mark.asyncio
async def test_chat_tool_approval_and_footer(tmp_path):
    script = [
        Message("assistant", [Text("Let me check."),
                              ToolCall("c1", "bash", json.dumps({"command": "echo hello-from-bash"}))]),
        Message("assistant", [Text("It printed **hello**.")], usage=Usage(900, 100, 0, 60, 0, 0.00007)),
    ]
    app = make_app(tmp_path, script)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"run it", "enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if isinstance(app.screen, Approval):
                break
        assert isinstance(app.screen, Approval)
        await pilot.press("y")
        await wait_idle(pilot, app)
        assert len(app.query(UserMsg)) == 1
        view = app.query_one(ToolView)
        assert "hello-from-bash" in str(view.query_one(".tool-out").render())
        assert app.agent.total.cost == pytest.approx(0.00007)
        app.save_screenshot(str(tmp_path / "shot.svg"))


@pytest.mark.asyncio
async def test_denied_and_history_replay(tmp_path):
    script = [Message("assistant", [ToolCall("c1", "write", json.dumps({"path": "x.txt", "content": "hi"}))]),
              Message("assistant", [Text("ok, not writing")])]
    app = make_app(tmp_path, script)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"write it", "enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if isinstance(app.screen, Approval):
                break
        await pilot.press("n")
        await wait_idle(pilot, app)
    assert not (tmp_path / "x.txt").exists()

    # reopening the same session shows the earlier conversation
    log = Log.load(app.session_log.path)
    again = DiwanApp(lambda lg, r: Agent(Fake([]), "m", lg, default_tools(), "sys"), log, tmp_path,
                     Router("openrouter"), Ref("openrouter", "m"))
    async with again.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        assert len(again.query(UserMsg)) == 1 and len(again.query(ToolView)) == 1


def _waiting_on_an_approval() -> bool:
    """True while some thread is still inside the app's approval wait."""
    import sys
    for top in sys._current_frames().values():
        frame = top
        while frame is not None:
            if frame.f_code.co_name == "_approve_from_thread":
                return True
            frame = frame.f_back
    return False


async def test_quitting_during_an_approval_lets_the_agent_thread_end(tmp_path):
    # before the fix, the agent thread waited forever on the dialog and the process never exited
    import time
    app = make_app(tmp_path, [Message("assistant", [ToolCall(
        "c1", "write", json.dumps({"path": "a.txt", "content": "x"}))])])
    async with app.run_test() as pilot:
        app.submit("write it")
        for _ in range(100):
            await pilot.pause(0.05)
            if isinstance(app.screen, Approval):
                break
        assert isinstance(app.screen, Approval) and _waiting_on_an_approval()
        await pilot.press("ctrl+q")
    deadline = time.monotonic() + 3
    while _waiting_on_an_approval() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _waiting_on_an_approval()             # the agent thread was released
    assert not (tmp_path / "a.txt").exists()         # and the pending write was refused


@pytest.mark.asyncio
async def test_typing_while_the_agent_works_queues_the_message(tmp_path):
    script = [Message("assistant", [ToolCall("c1", "bash", json.dumps({"command": "echo hi"}))]),
              Message("assistant", [Text("done, and checked the tests too")])]
    app = make_app(tmp_path, script)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"run it", "enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if isinstance(app.screen, Approval):
                break
        app.submit("also check the tests")          # typed while the agent waits on a tool
        await pilot.press("y")
        await wait_idle(pilot, app)
        assert len(app.query(UserMsg)) == 2
    texts = [(m.role, m.text) for m in app.session_log.messages()]
    assert texts[3] == ("user", "also check the tests") and texts[4][0] == "assistant"


@pytest.mark.asyncio
async def test_esc_after_queueing_puts_the_message_back(tmp_path):
    script = [Message("assistant", [ToolCall("c1", "bash", json.dumps({"command": "echo hi"}))]),
              Message("assistant", [Text("never")])]
    app = make_app(tmp_path, script)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"run it", "enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if isinstance(app.screen, Approval):
                break
        app.submit("actually, do something else")
        app.agent.interrupt()
        await pilot.press("y")
        await wait_idle(pilot, app)
        assert app.prompt.text == "actually, do something else"
        assert len(app.query(UserMsg)) == 1                  # nothing was sent by itself

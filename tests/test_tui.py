import json

import pytest
from tarjuman import Message, Text, ToolCall, Usage
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.models import Ref, Router
from diwan.tools import make_tools
from diwan.tui import Approval, DiwanApp, ToolView, UserMsg


def make_app(tmp_path, script):
    def make_agent(log, ref):
        return Agent(Fake(script), ref.model, log, make_tools(tmp_path), "sys")
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
        assert app.session.cost == pytest.approx(0.00007)
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
    again = DiwanApp(lambda lg, r: Agent(Fake([]), "m", lg, make_tools(tmp_path), "sys"), log, tmp_path,
                     Router("openrouter"), Ref("openrouter", "m"))
    async with again.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        assert len(again.query(UserMsg)) == 1 and len(again.query(ToolView)) == 1

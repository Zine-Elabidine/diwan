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
            if app.query(Approval):
                break
        assert app.query(Approval)
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
            if app.query(Approval):
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
            if app.query(Approval):
                break
        assert app.query(Approval) and _waiting_on_an_approval()
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
            if app.query(Approval):
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
            if app.query(Approval):
                break
        app.submit("actually, do something else")
        app.agent.interrupt()
        await pilot.press("y")
        await wait_idle(pilot, app)
        assert app.prompt.text == "actually, do something else"
        assert len(app.query(UserMsg)) == 1                  # nothing was sent by itself


@pytest.mark.asyncio
async def test_an_edit_shows_its_diff_in_the_dialog_in_the_chat_and_on_replay(tmp_path):
    from diwan.diffview import DiffLine, DiffView
    (tmp_path / "m.py").write_text("a = 1\nb = 2\nc = 3\n")
    script = [Message("assistant", [ToolCall("c0", "read", json.dumps({"path": "m.py"}))]),
              Message("assistant", [ToolCall("c1", "edit", json.dumps({"path": "m.py", "old": "b = 2",
                                                                       "new": "b = 20"}))]),
              Message("assistant", [Text("done")])]
    app = make_app(tmp_path, script)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"edit it", "enter")
        for _ in range(60):
            await pilot.pause(0.05)
            if app.query(Approval):
                break
        assert app.query(Approval)
        assert len(app.query(DiffView)) == 1   # the question sits under the real diff
        await pilot.press("y")
        await wait_idle(pilot, app)
        kinds = [d.row.kind for d in app.query(DiffLine)]
        assert kinds.count("del") == 1 and kinds.count("add") == 1 and "ctx" in kinds   # kept in the chat
    assert (tmp_path / "m.py").read_text() == "a = 1\nb = 20\nc = 3\n"

    again = DiwanApp(lambda lg, r: Agent(Fake([]), "m", lg, default_tools(), "sys"),
                     Log.load(app.session_log.path), tmp_path, Router("openrouter"), Ref("openrouter", "m"))
    async with again.run_test(size=(110, 40)) as pilot:
        await pilot.pause(0.3)
        rows = [(d.row.kind, d.row.text) for d in again.query(DiffLine)]
        assert ("del", "b = 2") in rows and ("add", "b = 20") in rows   # rebuilt from the file


@pytest.mark.asyncio
async def test_the_inline_question_answers_with_arrows_enter_and_esc(tmp_path):
    script = [Message("assistant", [ToolCall("c1", "bash", json.dumps({"command": "echo one"}))]),
              Message("assistant", [ToolCall("c2", "bash", json.dumps({"command": "echo two"}))]),
              Message("assistant", [ToolCall("c3", "write", json.dumps({"path": "n.txt", "content": "x"}))]),
              Message("assistant", [Text("done")])]
    app = make_app(tmp_path, script)

    async def question(pilot):
        for _ in range(60):
            await pilot.pause(0.05)
            if app.query(Approval):
                return app.query_one(Approval)
        raise AssertionError("no question")

    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"go", "enter")
        q = await question(pilot)
        assert app.focused is q and not isinstance(app.screen, Approval)   # inline, focused
        await pilot.press("down", "enter")             # 2. Yes, and don't ask again for bash
        await pilot.pause(0.3)
        assert "bash" in app.session.approvals.always   # so echo two runs without asking
        await question(pilot)                           # the write still asks
        await pilot.press("escape")                     # Esc says no
        await wait_idle(pilot, app)
        assert not (tmp_path / "n.txt").exists() and app.focused is app.prompt

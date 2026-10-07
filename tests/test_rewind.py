"""/rewind: back to before an earlier message, on a new branch; the old one stays in the log."""

from types import SimpleNamespace

import pytest
from tarjuman import Message, Text
from tarjuman.fake import Fake

from diwan import commands
from diwan.agent import Agent
from diwan.log import Log
from diwan.tools import default_tools
from diwan.tui import UserMsg
from test_tui import make_app, wait_idle


def chat(log):
    for i in range(3):
        log.add_message(Message.user(f"question {i}"))
        log.add_message(Message("assistant", [Text(f"answer {i}")]))


def test_rewind_starts_a_branch_before_the_message(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    chat(log)
    assert log.rewind(2) == "question 1"
    assert [m.text for m in log.messages()] == ["question 0", "answer 0"]
    log.add_message(Message.user("question 1, better"))
    again = Log.load(log.path)                                # resuming follows the new branch
    assert [m.text for m in again.messages()][-1] == "question 1, better"
    assert len([e for e in again.events if e.type == "message"]) == 7   # nothing was removed
    assert log.rewind(9) is None


def test_rewind_survives_a_restart_with_nothing_sent(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    chat(log)
    log.rewind()
    assert [m.text for m in Log.load(log.path).messages()][-1] == "answer 1"


def test_the_command(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    chat(log)
    agent = Agent(Fake([]), "m", log, default_tools(), "sys")
    s = SimpleNamespace(agent=agent, log=log, rewind=log.rewind)
    assert commands.run(s, "/rewind").effect == "rewind"
    assert commands.run(s, "/rewind x").kind == "error"
    assert commands.run(s, "/rewind", busy=True).kind == "error"


@pytest.mark.asyncio
async def test_the_app_redraws_and_puts_the_message_back(tmp_path):
    app = make_app(tmp_path, [Message("assistant", [Text("first")]),
                              Message("assistant", [Text("second")])])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"one", "enter")
        await wait_idle(pilot, app)
        await pilot.press(*"two", "enter")
        await wait_idle(pilot, app)
        assert len(app.query(UserMsg)) == 2
        app.command("/rewind")
        await pilot.pause(0.2)
        assert len(app.query(UserMsg)) == 1 and app.prompt.text == "two"


def test_rewinding_past_a_model_switch_keeps_the_current_model(tmp_path):
    from diwan.models import Ref, Router
    from diwan.session import Session

    log = Log.new(cwd=str(tmp_path), provider="openrouter", model="old-model")
    log.add_message(Message.user("question 0"))
    log.add_message(Message("assistant", [Text("answer 0")]))
    s = Session(lambda lg, ref: Agent(Fake([]), ref.model, lg, default_tools(), "sys"), log,
                Ref("openrouter", "old-model"), Router("openrouter"), tmp_path)
    s.agent.use(Fake([]), "new-model")
    log.add_message(Message.user("question 1"))
    assert s.rewind(2) == "question 0"
    assert Log.load(log.path).current_model()[1] == "new-model"

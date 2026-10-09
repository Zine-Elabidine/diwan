"""The room: sessions tiled on one screen, in rooms switched from a sidebar."""

import json

from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.models import Ref, Router
from diwan.room import RoomApp
from diwan.tools import default_tools
from diwan.tui import Approval, SessionPane, UserMsg


def make_room(tmp_path, scripts=()):
    """A room app whose n-th session plays scripts[n] (or says "ok")."""
    made = []

    def make_pane():
        script = scripts[len(made)] if len(made) < len(scripts) else [
            Message("assistant", [Text("ok")])]
        pane = SessionPane(lambda lg, r: Agent(Fake(script), r.model, lg, default_tools(), "sys"),
                           Log.new(cwd=str(tmp_path)), tmp_path, Router("openrouter"),
                           Ref("openrouter", "deepseek/deepseek-v4-flash"))
        made.append(pane)
        return pane
    return RoomApp(make_pane), made


async def settle(pilot, app, n=40):
    for _ in range(n):
        await pilot.pause(0.05)
        if not any(p.running for r in app.rooms for p in r.panes):
            return


async def test_sessions_tile_master_and_stack(tmp_path):
    app, _ = make_room(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert len(app.room.panes) == 1 and app.room.styles.grid_size_columns == 1
        await pilot.press("alt+s")
        await pilot.pause()
        assert app.room.styles.grid_size_columns == 2 and app.room.styles.grid_size_rows == 1
        await pilot.press("alt+s")
        await pilot.pause()
        first, second, third = app.room.panes
        assert app.room.styles.grid_size_rows == 2 and first.styles.row_span == 2
        assert first.region.x < second.region.x and second.region.y < third.region.y
        assert app.pane is third                       # a new session gets the focus


async def test_move_focus_and_zoom(tmp_path):
    app, _ = make_room(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("alt+s")
        await pilot.press("alt+s")
        await pilot.pause()
        first, second, third = app.room.panes
        await pilot.press("alt+h")
        assert app.pane is first
        await pilot.press("alt+l")
        assert app.pane in (second, third)
        await pilot.press("alt+j")
        assert app.pane is third
        await pilot.press("alt+z")
        await pilot.pause()
        assert app.room.zoomed is third and not first.display and third.display
        await pilot.press("alt+z")
        await pilot.pause()
        assert app.room.zoomed is None and first.display


async def test_rooms_switch_and_keep_their_sessions(tmp_path):
    app, _ = make_room(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("alt+n")
        await pilot.pause()
        assert len(app.rooms) == 2 and app.room is app.rooms[1]
        await pilot.press("alt+s")
        await pilot.pause()
        await pilot.press("alt+r")
        await pilot.pause()
        assert app.room is app.rooms[0] and len(app.room.panes) == 1
        assert len(app.rooms[1].panes) == 2          # still there, still running if they were


async def test_each_session_runs_its_own_turn(tmp_path):
    app, _ = make_room(tmp_path, [[Message("assistant", [Text("from one")])],
                                     [Message("assistant", [Text("from two")])]])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("alt+s")
        await pilot.pause()
        one, two = app.room.panes
        one.submit("hi one")
        two.submit("hi two")
        await settle(pilot, app)
        assert [m.text for m in one.session_log.messages()] == ["hi one", "from one"]
        assert [m.text for m in two.session_log.messages()] == ["hi two", "from two"]
        assert one.session_log.id != two.session_log.id
        assert len(one.query(UserMsg)) == 1


async def test_close_with_alt_q_or_exit(tmp_path):
    app, _ = make_room(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("alt+s")
        await pilot.pause()
        await pilot.press("alt+q")
        await pilot.pause()
        assert len(app.room.panes) == 1
        app.room.panes[0].submit("/exit")
        await pilot.pause(0.2)
        assert app.room.panes == [] and app.room.query_one(".empty").display


async def test_an_approval_says_which_session_asks(tmp_path):
    write = [Message("assistant", [ToolCall("c1", "write", json.dumps({"path": "a.txt", "content": "A"}))]),
             Message("assistant", [Text("written")])]
    app, _ = make_room(tmp_path, [write])
    async with app.run_test(size=(160, 50)) as pilot:
        pane = app.room.panes[0]
        pane.submit("write it")
        for _ in range(50):
            await pilot.pause(0.05)
            if isinstance(app.screen, Approval):
                break
        assert isinstance(app.screen, Approval) and app.screen.who == pane.label
        await pilot.press("y")
        await settle(pilot, app)
        assert (tmp_path / "a.txt").read_text() == "A"


def test_every_alt_key_reaches_the_app_as_alt():
    # a terminal sends Alt+x as ESC x; some of those decode as other keys (alt+enter as enter,
    # alt+f as ctrl+right), and the binding would never fire outside the tests
    from textual._xterm_parser import XTermParser

    for b in RoomApp.BINDINGS:
        if b.key.startswith("alt+"):
            p = XTermParser()
            keys = [getattr(e, "key", None) for e in [*p.feed("\x1b" + b.key[4:]), *p.feed("")]]
            assert keys == [b.key], (b.key, keys)

"""The room: sessions tiled on one screen, in rooms switched from a sidebar."""

import json

from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.models import Ref, Router
from diwan.room import RoomApp
from diwan.tools import default_tools
from diwan.tui import Approval, SessionPane, UserMsg, pane_of


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


async def test_a_new_session_splits_the_focused_one(tmp_path):
    app, _ = make_room(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        (one,) = app.room.panes
        assert one.region.width > 100                   # alone, it fills the room
        await pilot.press("alt+s")
        await pilot.pause()
        one, two = app.room.panes
        assert one.region.x < two.region.x and one.region.y == two.region.y   # wide: side by side
        await pilot.press("alt+s")
        await pilot.pause()
        one, two, three = app.room.panes
        assert two.region.x == three.region.x and two.region.y < three.region.y   # tall: stacked
        assert app.pane is three                       # a new session gets the focus


async def test_dock_swap_and_drag_a_split(tmp_path):
    from diwan.room import Bar
    app, _ = make_room(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("alt+s")
        await pilot.press("alt+s")
        await pilot.pause()
        one, two, three = app.room.panes
        app.room.dock(three, one, "bottom")            # three goes under one
        await pilot.pause()
        assert three.region.x == one.region.x and three.region.y > one.region.y
        assert two.region.height > one.region.height   # two now has the whole right side
        app.room.swap(one, two)
        await pilot.pause()
        assert two.region.x < one.region.x
        bar = next(b for b in app.room.query(Bar) if b.axis == "x")
        before = two.region.width
        app.room.drag_split(bar, bar.region.x + 20)    # what a drag of 20 cells to the right does
        await pilot.pause()
        assert two.region.width >= before + 18


async def test_dragging_a_title_onto_an_edge_docks_it(tmp_path):
    app, _ = make_room(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.press("alt+s")
        await pilot.press("alt+s")
        await pilot.pause()
        one, _, three = app.room.panes
        r, room = one.region, app.room.region
        await pilot.mouse_down(app.room, offset=(three.region.x + 4 - room.x, three.region.y - room.y))
        assert app.room._moving is three
        app.room._track(r.x + r.width // 2, r.bottom - 2)   # over the bottom of one
        assert app.room._target == (one, "bottom")
        app.room.on_mouse_up(None)
        await pilot.pause()
        assert three.region.y > one.region.y and three.region.x == one.region.x


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
            if app.query(Approval):
                break
        assert pane_of(app.query_one(Approval)) is pane   # asked inside the session that wants it
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
            ch = b.key[4:]
            ch = ch[6:].upper() if ch.startswith("shift+") else ch   # alt+shift+h is ESC H
            keys = [getattr(e, "key", None) for e in [*p.feed("\x1b" + ch), *p.feed("")]]
            assert keys == [b.key], (b.key, keys)

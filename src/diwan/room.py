"""`diwan --room`: several sessions on one screen, tiled like a tiling window manager, in
rooms you switch between from a sidebar. Every session works in the same folder (the War
Room's shared working tree); each has its own log, model and approvals. Sessions keep running
in rooms you are not looking at; the sidebar marks a room whose session waits for you.

Layout: one session fills the room; two split it; from three on, the first is the master on
the left and the others stack on the right. Alt+Z zooms the focused one."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from rich.text import Text as RText
from tarjuman import TarjumanError
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Label, ListItem, ListView, Static

from .theme import AMBER, BG, CYAN, DIM, DIWAN, GREEN, INK
from .tui import SessionPane, pane_of

# only keys a terminal really delivers as Alt: alt+enter arrives as plain enter, alt+f and
# alt+b as word moves (readline), alt+1 as a symbol (tests/test_room.py checks the rest)
KEYS = "alt+s new  ·  alt+hjkl move  ·  alt+z zoom  ·  alt+q close  ·  alt+n room  ·  alt+r/e switch "

MARKS = {"thinking": ("●", CYAN), "running": ("●", CYAN), "waiting": ("◆", AMBER),
         "idle": ("●", GREEN)}


class Room(Container):
    """The sessions of one room, tiled."""

    DEFAULT_CSS = """
    Room { layout: grid; grid-gutter: 1 2; padding: 1 2 0 2; height: 1fr; }
    Room > SessionPane { border: round $rule; border-title-color: $dim; opacity: 75%; }
    Room > SessionPane:focus-within { border: round $accent; border-title-color: $accent;
                                      border-title-style: bold; opacity: 100%; }
    Room > .empty { color: $dim; content-align: center middle; height: 1fr; width: 1fr; }
    """

    def __init__(self, name: str, room_id: str):
        super().__init__(id=room_id)
        self.title = name
        self.zoomed: SessionPane | None = None

    @property
    def panes(self) -> list[SessionPane]:
        return list(self.query_children(SessionPane))

    def relayout(self) -> None:
        panes = self.panes
        for hint in self.query_children(".empty"):
            hint.display = not panes
        if self.zoomed not in panes:
            self.zoomed = None
        shown = [self.zoomed] if self.zoomed else panes
        for p in panes:
            p.display = p in shown
            p.styles.row_span = 1
        n = len(shown)
        self.styles.grid_size_columns = 1 if n <= 1 else 2
        self.styles.grid_size_rows = max(1, n - 1)
        if n >= 3:
            shown[0].styles.row_span = n - 1       # the master, left; the rest stack right
        for p in panes:
            p.border_title = f" {p.label} "


class RoomApp(App):
    TITLE = "diwan room"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar { width: 24; background: $surface; padding: 1 0 0 0; }
    #brand { padding: 0 2; height: 1; }
    #sidebar-title { padding: 0 2; color: $dim; height: 1; margin: 1 0 0 0; }
    #rooms { height: 1fr; background: transparent; margin: 0 0 0 0; }
    #rooms > ListItem { padding: 0 1 0 1; background: transparent; border-left: outer $surface; }
    #rooms > ListItem.-highlight { background: $boost; border-left: outer $accent; }
    #rooms:focus > ListItem.-highlight { background: $boost; }
    #stage { width: 1fr; height: 1fr; }
    #bar { height: 1; padding: 0 1; background: $surface; }
    #bar-left { width: 1fr; }
    #bar-right { width: auto; color: $dim; }
    """
    BINDINGS: ClassVar = [
        Binding("alt+s", "new_session", "New session", priority=True),
        Binding("alt+q", "close_session", "Close", priority=True),
        Binding("alt+z", "zoom", "Zoom", priority=True),
        Binding("alt+h", "move('left')", show=False, priority=True),
        Binding("alt+l", "move('right')", show=False, priority=True),
        Binding("alt+k", "move('up')", show=False, priority=True),
        Binding("alt+j", "move('down')", show=False, priority=True),
        Binding("alt+n", "new_room", "New room", priority=True),
        Binding("alt+r", "next_room(1)", "Next room", priority=True),
        Binding("alt+e", "next_room(-1)", "Previous room", priority=True),
        Binding("escape", "interrupt", "Stop", priority=True),
        Binding("ctrl+t", "toggle_think", "Reasoning"),
        Binding("ctrl+b", "toggle_agents", "Agents"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, make_pane: Callable[[], SessionPane]):
        """make_pane: a new session (its own log, in the room's folder)."""
        super().__init__()
        self.register_theme(DIWAN)
        self.theme = "diwan"
        self.make_pane = make_pane
        self._count = 0

    # --- layout -----------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static(RText.assemble(("◆ ", AMBER), ("diwan", f"bold {INK}")), id="brand")
                yield Static("ROOMS", id="sidebar-title")
                yield ListView(id="rooms")
            yield ContentSwitcher(id="stage")
        with Horizontal(id="bar"):
            yield Static(id="bar-left")
            yield Static(KEYS, id="bar-right")

    async def on_mount(self) -> None:
        await self.action_new_room()
        self.set_interval(0.5, self._refresh_bar)

    @property
    def rooms(self) -> list[Room]:
        return list(self.query_one("#stage", ContentSwitcher).query_children(Room))

    @property
    def room(self) -> Room:
        stage = self.query_one("#stage", ContentSwitcher)
        return stage.get_child_by_id(stage.current or "", Room)

    @property
    def pane(self) -> SessionPane | None:
        """The focused session, else the room's first."""
        p = pane_of(self.focused)
        if p is not None and p.parent is self.room:
            return p
        panes = self.room.panes
        return panes[0] if panes else None

    def _refresh_bar(self) -> None:
        for room in self.rooms:
            for p in room.panes:
                title = f" {p.label} "
                if p.border_title != title:
                    p.border_title = title
        t = RText.assemble((f" {self.room.title} ", f"bold {BG} on {AMBER}"), "  ")
        for p in self.room.panes:
            mark, color = MARKS.get(p.state, ("○", DIM))
            t.append(f"{mark} ", style=color)
            t.append(f"{p.label}  ", style=f"bold {INK}" if p is self.pane else DIM)
        self.query_one("#bar-left", Static).update(t)
        rooms = self.query_one("#rooms", ListView)
        for item, room in zip(rooms.children, self.rooms, strict=False):
            here = room is self.room
            line = RText.assemble((room.title, f"bold {INK}" if here else DIM), "  ")
            for p in room.panes:   # one dot per session, colored by what it is doing
                line.append(MARKS.get(p.state, ("○", DIM))[0], style=MARKS.get(p.state, ("", DIM))[1])
            if any(p.state == "waiting" for p in room.panes):
                line.append("  needs you", style=AMBER)
            item.query_one(Label).update(line)

    # --- rooms ------------------------------------------------------------------------------

    async def action_new_room(self) -> None:
        self._count += 1
        room = Room(f"room {self._count}", f"room-{self._count}")
        stage = self.query_one("#stage", ContentSwitcher)
        await stage.mount(room)
        await room.mount(Static("No session here. alt+s starts one.", classes="empty"))
        await self.query_one("#rooms", ListView).append(ListItem(Label(room.title)))
        self.show_room(room)
        await self.action_new_session()

    def show_room(self, room: Room) -> None:
        self.query_one("#stage", ContentSwitcher).current = room.id
        rooms = self.query_one("#rooms", ListView)
        i = self.rooms.index(room)
        if rooms.index != i:
            rooms.index = i
        panes = room.panes
        if panes:
            panes[0].prompt.focus()
        self._refresh_bar()

    def action_next_room(self, step: int) -> None:
        rooms = self.rooms
        self.show_room(rooms[(rooms.index(self.room) + step) % len(rooms)])

    @on(ListView.Selected, "#rooms")
    def _room_picked(self, event: ListView.Selected) -> None:
        i = event.list_view.index
        if i is not None and i < len(self.rooms) and self.rooms[i] is not self.room:
            self.show_room(self.rooms[i])

    # --- sessions ---------------------------------------------------------------------------

    async def action_new_session(self) -> None:
        room = self.room
        try:
            pane = self.make_pane()
        except TarjumanError as e:   # a missing key, an unknown provider
            self.notify(str(e), severity="error")
            return
        room.zoomed = None
        await room.mount(pane)
        room.relayout()
        pane.prompt.focus()

    async def action_close_session(self) -> None:
        pane = self.pane
        if pane is not None:
            await self._close(pane)

    @on(SessionPane.Closed)
    async def _session_closed(self, event: SessionPane.Closed) -> None:
        await self._close(event.pane)

    async def _close(self, pane: SessionPane) -> None:
        room = pane.parent
        await pane.remove()   # its turn stops and waiting approvals answer "no" (on_unmount)
        if isinstance(room, Room):
            room.relayout()
            rest = room.panes
            if rest:
                rest[0].prompt.focus()

    def action_zoom(self) -> None:
        room, pane = self.room, self.pane
        if pane is None:
            return
        room.zoomed = None if room.zoomed is pane else pane
        room.relayout()
        pane.prompt.focus()

    def action_move(self, direction: str) -> None:
        """Focus the nearest session in that direction, by where they sit on screen."""
        here = self.pane
        if here is None:
            return
        r = here.region
        cx, cy = r.x + r.width / 2, r.y + r.height / 2
        best, best_d = None, None
        for p in self.room.panes:
            if p is here or not p.display:
                continue
            q = p.region
            px, py = q.x + q.width / 2, q.y + q.height / 2
            dx, dy = px - cx, py - cy
            ahead = {"left": dx < 0, "right": dx > 0, "up": dy < 0, "down": dy > 0}[direction]
            if not ahead:
                continue
            along, across = (abs(dx), abs(dy)) if direction in ("left", "right") else (abs(dy), abs(dx))
            d = along + 2 * across
            if best_d is None or d < best_d:
                best, best_d = p, d
        if best is not None:
            best.prompt.focus()

    def action_interrupt(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss("no")
            return
        if self.pane is not None:
            self.pane.interrupt()

    def action_toggle_think(self) -> None:
        if self.pane is not None:
            self.pane.action_toggle_think()

    def action_toggle_agents(self) -> None:
        if self.pane is not None:
            self.pane.action_toggle_agents()


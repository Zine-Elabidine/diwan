"""`diwan --room`: several sessions on one screen, tiled like a tiling window manager, in
rooms you switch between from a sidebar. Every session works in the same folder (the War
Room's shared working tree); each has its own log, model and approvals. Sessions keep running
in rooms you are not looking at; the sidebar card of a room shows a dot per session, and says
when one waits for you.

Layout: the sessions of a room are the leaves of a tree of splits (side by side, or stacked),
like Hyprland or i3. A new session splits the focused one. Every split has a bar you drag to
resize (only a guide line follows the mouse; the tiles resize once, on release), and where two
bars meet you drag both. Drag a session by its title onto another one: near an edge it docks
on that side, in the middle the two swap. The tiles are placed with absolute offsets, so moving
one never re-creates it. F3 switches between keeping your sizes ("manual") and growing the
focused session ("focus"); Alt+Z zooms the focused one."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text as RText
from tarjuman import TarjumanError
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static

from .theme import Ink, ink
from .themeui import ThemedApp
from .tui import SessionPane, pane_of

# only keys a terminal really delivers as Alt: alt+enter arrives as plain enter, alt+f and
# alt+b as word moves (readline), alt+1 as a symbol (tests/test_room.py checks the rest)
KEYS = ("alt+s new · drag a title to move · alt+hjkl focus · alt+shift+hjkl swap · alt+z zoom · "
        "F3 layout · F2 theme")

GRIP = "⠿"


def mark(state: str, c: Ink) -> tuple[str, str]:
    return {"thinking": ("●", c.secondary), "running": ("●", c.secondary),
            "waiting": ("◆", c.warning), "idle": ("●", c.success)}.get(state, ("○", c.dim))


# --- the layout tree ----------------------------------------------------------------------------

@dataclass(eq=False)
class Leaf:
    pane: SessionPane


@dataclass(eq=False)
class Split:
    """dir "h": side by side; "v": stacked. ratio: the first child's share."""
    dir: str
    a: Leaf | Split
    b: Leaf | Split
    ratio: float = 0.5


Node = Leaf | Split


def leaves(n: Node | None) -> list[SessionPane]:
    if n is None:
        return []
    return [n.pane] if isinstance(n, Leaf) else leaves(n.a) + leaves(n.b)


def leaf_nodes(n: Node | None) -> list[Leaf]:
    if n is None:
        return []
    return [n] if isinstance(n, Leaf) else leaf_nodes(n.a) + leaf_nodes(n.b)


def splits(n: Node | None) -> list[Split]:
    if n is None or isinstance(n, Leaf):
        return []
    return [n, *splits(n.a), *splits(n.b)]


def without(n: Node | None, pane: SessionPane) -> Node | None:
    """The tree without `pane`; its sibling takes the parent's place."""
    if n is None:
        return None
    if isinstance(n, Leaf):
        return None if n.pane is pane else n
    a, b = without(n.a, pane), without(n.b, pane)
    if a is None or b is None:
        return a or b
    n.a, n.b = a, b
    return n


def replaced(n: Node, pane: SessionPane, new: Node) -> Node:
    if isinstance(n, Leaf):
        return new if n.pane is pane else n
    n.a, n.b = replaced(n.a, pane, new), replaced(n.b, pane, new)
    return n


def path(n: Node | None, pane: SessionPane) -> list[tuple[Split, str]] | None:
    """The splits from the root down to `pane`, with the side ("a" or "b") taken at each."""
    if n is None:
        return None
    if isinstance(n, Leaf):
        return [] if n.pane is pane else None
    for side, child in (("a", n.a), ("b", n.b)):
        rest = path(child, pane)
        if rest is not None:
            return [(n, side), *rest]
    return None


# --- dragging -----------------------------------------------------------------------------------

class Guide(Widget):
    """The line that follows the mouse while a bar is dragged, in half cells (▌▐ / ▀▄): kitty
    and other terminals report the mouse finer than a cell, so it glides twice as finely."""
    DEFAULT_CSS = "Guide { position: absolute; layer: overlay; color: $accent; background: transparent; }"

    def __init__(self, axis: str):
        super().__init__()
        self.axis, self.half = axis, 0

    def render(self) -> str:
        if self.axis == "x":
            return "\n".join(["▌▐"[self.half]] * max(1, self.size.height))
        return "▀▄"[self.half] * max(1, self.size.width)


class Overlay(Static):
    """While a session is dragged: the zone it would land in, and its name by the mouse."""
    DEFAULT_CSS = """
    Overlay { position: absolute; layer: overlay; }
    Overlay.zone { background: $accent 22%; border: round $accent; content-align: center middle;
                   color: $accent; }
    Overlay.ghost { width: auto; height: 1; padding: 0 1; background: $accent; color: $background;
                    text-style: bold; }
    """


class Bar(Widget):
    """A 1-cell bar you drag: between two tiles (`split`), or the sidebar's edge (`apply`)."""
    DEFAULT_CSS = """
    Bar { background: $background; }
    Bar.x { width: 1; pointer: ew-resize; }
    Bar.y { height: 1; pointer: ns-resize; }
    Bar:hover, Bar.dragging { background: $accent 45%; }
    Room > Bar { position: absolute; }
    """

    def __init__(self, axis: str, split: Split | None = None,
                 apply: Callable[[int], None] | None = None, **kw):
        super().__init__(classes=axis, **kw)
        self.axis, self.split, self.apply = axis, split, apply
        self.guides: dict[Bar, Guide] = {}
        self.pos: dict[Bar, int] = {}

    def render(self) -> str:
        return ""

    def _partner(self, x: int, y: int) -> Bar | None:
        """A bar of the other direction that meets this one at (x, y): drag both at once."""
        if self.split is None or not isinstance(self.parent, Room):
            return None
        for o in self.parent.query_children(Bar):
            if o.axis == self.axis or not o.display:
                continue
            r = o.region
            if self.axis == "x" and abs(y - r.y) <= 1 and (abs(x - r.x) <= 1 or abs(x - r.right + 1) <= 1):
                return o
            if self.axis == "y" and abs(x - r.x) <= 1 and (abs(y - r.y) <= 1 or abs(y - r.bottom + 1) <= 1):
                return o
        return None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self.guides:
            self.styles.pointer = "nwse-resize" if self._partner(event.screen_x, event.screen_y) else \
                ("ew-resize" if self.axis == "x" else "ns-resize")
        elif (event.screen_x, event.screen_y) != (0, 0):
            self._follow(event.pointer_screen_x, event.pointer_screen_y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse()
        self.add_class("dragging")
        partner = self._partner(event.screen_x, event.screen_y)
        for bar in [self, *([partner] if partner else [])]:
            guide = Guide(bar.axis)
            self.screen.mount(guide)
            r = bar.region
            guide.styles.width, guide.styles.height = (1, r.height) if bar.axis == "x" else (r.width, 1)
            self.guides[bar] = guide
        self._follow(event.pointer_screen_x, event.pointer_screen_y)

    def _follow(self, fx: float, fy: float) -> None:
        for bar, guide in self.guides.items():
            v = fx if bar.axis == "x" else fy
            cell, guide.half = int(v), int(v - int(v) >= 0.5)
            self.pos[bar] = cell + guide.half
            r = bar.region
            guide.styles.offset = (cell, r.y) if bar.axis == "x" else (r.x, cell)
            guide.refresh()

    def on_mouse_up(self, _: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        for guide in self.guides.values():
            guide.remove()
        pos, self.guides, self.pos = self.pos, {}, {}
        for bar, v in pos.items():
            if bar.apply is not None:
                bar.apply(v)
            elif isinstance(bar.parent, Room):
                bar.parent.drag_split(bar, v)


# --- a room -------------------------------------------------------------------------------------

class Room(Container):
    """The sessions of one room, placed by its layout tree."""

    DEFAULT_CSS = """
    Room { padding: 1 2 0 2; height: 1fr; }
    Room > SessionPane { position: absolute; border: round $rule; border-title-color: $dim; opacity: 80%; }
    Room > SessionPane:focus-within { border: round $accent; border-title-color: $accent;
                                      border-title-style: bold; opacity: 100%; }
    Room > .empty { color: $dim; content-align: center middle; height: 1fr; width: 1fr; }
    """

    def __init__(self, name: str, room_id: str):
        super().__init__(id=room_id)
        self.title = name
        self.split_tree: Node | None = None
        self.zoomed: SessionPane | None = None
        self.mode = "manual"            # "manual": keep sizes; "focus": the focused session grows
        self._bars: dict[int, Bar] = {}  # id(split) -> its bar
        self._rects: dict[int, tuple[int, int, int, int]] = {}   # id(split) -> x, y, w, h
        self._moving: SessionPane | None = None
        self._ghost: Overlay | None = None
        self._zone: Overlay | None = None
        self._target: tuple[SessionPane, str] | None = None

    @property
    def panes(self) -> list[SessionPane]:
        return leaves(self.split_tree)

    # --- the tree -----------------------------------------------------------------------------

    async def add(self, pane: SessionPane, near: SessionPane | None = None) -> None:
        """Mount `pane`, splitting `near` (the focused session) along its longer side."""
        await self.mount(pane)
        if self.split_tree is None:
            self.split_tree = Leaf(pane)
        else:
            near = near if near in self.panes else self.panes[-1]
            r = near.region
            d = "h" if r.height == 0 or r.width >= r.height * 2.2 else "v"
            self.split_tree = replaced(self.split_tree, near, Split(d, Leaf(near), Leaf(pane)))
        self.zoomed = None
        self.relayout()

    def forget(self, pane: SessionPane) -> None:
        self.split_tree = without(self.split_tree, pane)
        if self.zoomed is pane:
            self.zoomed = None

    def swap(self, one: SessionPane, two: SessionPane) -> None:
        for leaf in leaf_nodes(self.split_tree):
            leaf.pane = two if leaf.pane is one else one if leaf.pane is two else leaf.pane
        self.relayout()

    def dock(self, pane: SessionPane, target: SessionPane, side: str) -> None:
        """Move `pane` next to `target`: "left", "right", "top" or "bottom" of it."""
        if pane is target:
            return
        rest = without(self.split_tree, pane)
        if rest is None:
            return
        first, second = (Leaf(pane), Leaf(target)) if side in ("left", "top") else (Leaf(target), Leaf(pane))
        d = "h" if side in ("left", "right") else "v"
        self.split_tree = replaced(rest, target, Split(d, first, second))
        self.relayout()

    # --- placing --------------------------------------------------------------------------------

    def relayout(self) -> None:
        panes = self.panes
        for hint in self.query_children(".empty"):
            hint.display = not panes
        if self.zoomed not in panes:
            self.zoomed = None
        live = {id(s) for s in splits(self.split_tree)}
        for key in [k for k in self._bars if k not in live]:
            self._bars.pop(key).remove()
        if self.mode == "focus":
            self._grow_focused()
        w, h = self.content_size
        if w <= 0 or h <= 0 or self.split_tree is None:
            return
        self._rects.clear()
        for p in panes:
            p.border_title = f" {GRIP} {p.label} "
        if self.zoomed is not None:
            for p in panes:
                p.display = p is self.zoomed
            self._put(self.zoomed, 0, 0, w, h)
            for bar in self._bars.values():
                bar.display = False
            return
        for p in panes:
            p.display = True
        self._place(self.split_tree, 0, 0, w, h)

    def _put(self, pane: SessionPane, x: int, y: int, w: int, h: int) -> None:
        pane.styles.offset = (x, y)
        pane.styles.width, pane.styles.height = max(1, w), max(1, h)

    def _place(self, n: Node, x: int, y: int, w: int, h: int) -> None:
        if isinstance(n, Leaf):
            self._put(n.pane, x, y, w, h)
            return
        self._rects[id(n)] = (x, y, w, h)
        axis = "x" if n.dir == "h" else "y"
        bar = self._bars.get(id(n))
        if bar is None or bar.axis != axis:
            if bar is not None:
                bar.remove()
            bar = Bar(axis, split=n)
            self._bars[id(n)] = bar
            self.mount(bar)
        bar.split, bar.display = n, True
        if n.dir == "h":
            wa = max(4, min(w - 5, round((w - 1) * n.ratio)))
            self._place(n.a, x, y, wa, h)
            bar.styles.offset, bar.styles.width, bar.styles.height = (x + wa, y), 1, h
            self._place(n.b, x + wa + 1, y, w - wa - 1, h)
        else:
            ha = max(3, min(h - 4, round((h - 1) * n.ratio)))
            self._place(n.a, x, y, w, ha)
            bar.styles.offset, bar.styles.width, bar.styles.height = (x, y + ha), w, 1
            self._place(n.b, x, y + ha + 1, w, h - ha - 1)

    def on_resize(self) -> None:
        self.relayout()

    def drag_split(self, bar: Bar, pos: int) -> None:
        """A bar was dropped at screen position `pos`: set its split's ratio from it."""
        s = bar.split
        if s is None or id(s) not in self._rects:
            return
        x, y, w, h = self._rects[id(s)]
        origin = self.content_region.x + x if bar.axis == "x" else self.content_region.y + y
        span = w if bar.axis == "x" else h
        s.ratio = max(0.1, min(0.9, (pos - origin) / max(1, span - 1)))
        self.mode = "manual"
        self.relayout()

    def even(self) -> None:
        for s in splits(self.split_tree):
            s.ratio = 0.5
        self.relayout()

    def _grow_focused(self) -> None:
        here = pane_of(self.app.focused)
        if here is None:
            return
        for s, side in path(self.split_tree, here) or []:
            s.ratio = 0.62 if side == "a" else 0.38

    # --- drag a session by its title ------------------------------------------------------------

    def on_mouse_down(self, event: events.MouseDown) -> None:
        for p in self.panes:
            r = p.region
            if p.display and r.y == event.screen_y and r.x <= event.screen_x < r.right:
                self._moving = p
                self.capture_mouse()
                self._ghost = Overlay(f"{GRIP} {p.label}", classes="ghost")
                self._zone = Overlay("", classes="zone")
                self.screen.mount(self._ghost, self._zone)
                self._zone.display = False
                self._track(event.screen_x, event.screen_y)
                event.stop()
                return

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._moving is not None and (event.screen_x, event.screen_y) != (0, 0):
            self._track(event.screen_x, event.screen_y)

    def _track(self, x: int, y: int) -> None:
        if self._ghost is None or self._zone is None:
            return
        self._ghost.styles.offset = (x + 2, y)
        over = next((p for p in self.panes if p is not self._moving and p.display
                     and p.region.contains(x, y)), None)
        if over is None:
            self._zone.display, self._target = False, None
            return
        r = over.region
        fx, fy = (x - r.x) / max(1, r.width), (y - r.y) / max(1, r.height)
        near, side = min((fx, "left"), (1 - fx, "right"), (fy, "top"), (1 - fy, "bottom"))
        side = side if near < 0.3 else "swap"
        zx, zy, zw, zh = r.x, r.y, r.width, r.height
        if side == "left":
            zw = r.width // 2
        elif side == "right":
            zx, zw = r.x + r.width // 2, r.width - r.width // 2
        elif side == "top":
            zh = r.height // 2
        elif side == "bottom":
            zy, zh = r.y + r.height // 2, r.height - r.height // 2
        self._zone.styles.offset = (zx, zy)
        self._zone.styles.width, self._zone.styles.height = zw, zh
        self._zone.update("swap" if side == "swap" else "")
        self._zone.display = True
        self._target = (over, side)

    def on_mouse_up(self, _: events.MouseUp) -> None:
        moving, self._moving = self._moving, None
        if moving is None:
            return
        self.release_mouse()
        for o in (self._ghost, self._zone):
            if o is not None:
                o.remove()
        self._ghost = self._zone = None
        target, self._target = self._target, None
        if target is None:
            return
        over, side = target
        if side == "swap":
            self.swap(moving, over)
        else:
            self.dock(moving, over, side)
        moving.prompt.focus()


class RoomCard(Static):
    """A room in the sidebar: its name, how many sessions, a dot per session."""

    DEFAULT_CSS = """
    RoomCard { width: 100%; height: auto; min-height: 5; content-align: center middle;
               text-align: center; border: round $panel; background: $surface; margin: 0 0 1 0; }
    RoomCard:hover { background: $panel; }
    RoomCard.current { border: round $accent; background: $panel; }
    """

    def __init__(self, room: Room):
        super().__init__()
        self.room = room

    def show(self, current: bool) -> None:
        c = ink(self.app.current_theme)
        n = len(self.room.panes)
        t = RText(self.room.title, style=f"bold {c.ink}" if current else c.ink, justify="center")
        t.append(f"\n{n} session{'s' if n != 1 else ''}\n", style=c.dim)
        for p in self.room.panes:
            m, color = mark(p.state, c)
            t.append(f"{m} ", style=color)
        if not self.room.panes:
            t.append("empty", style=c.dim)
        if any(p.state == "waiting" for p in self.room.panes):
            t.append("\nneeds you", style=f"bold {c.warning}")
        self.set_class(current, "current")
        self.update(t)

    def on_click(self) -> None:
        app = self.app
        if isinstance(app, RoomApp):
            app.show_room(self.room)


class NewRoom(Static):
    async def on_click(self) -> None:
        app = self.app
        if isinstance(app, RoomApp):
            await app.action_new_room()


class RoomApp(ThemedApp):
    TITLE = "diwan room"
    CSS = """
    Screen { layout: vertical; layers: base overlay; }
    #body { height: 1fr; }
    #sidebar { width: 18; background: $surface; padding: 1 1 0 1; }
    #brand { height: 2; width: 100%; content-align: center top; }
    #sidebar-title { color: $dim; height: 1; width: 100%; content-align: center middle; margin: 0 0 1 0; }
    #rooms { height: 1fr; background: transparent; scrollbar-size-vertical: 0; }
    #newroom { width: 100%; height: 3; content-align: center middle; color: $dim; border: dashed $panel; }
    #newroom:hover { color: $foreground; }
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
        Binding("alt+shift+h", "swap('left')", show=False, priority=True),
        Binding("alt+shift+l", "swap('right')", show=False, priority=True),
        Binding("alt+shift+k", "swap('up')", show=False, priority=True),
        Binding("alt+shift+j", "swap('down')", show=False, priority=True),
        Binding("alt+n", "new_room", "New room", priority=True),
        Binding("alt+r", "next_room(1)", "Next room", priority=True),
        Binding("alt+e", "next_room(-1)", "Previous room", priority=True),
        Binding("alt+w", "toggle_sidebar", "Sidebar", priority=True),
        Binding("f3", "layout", "Layout", priority=True),
        Binding("escape", "interrupt", "Stop", priority=True),
        Binding("ctrl+t", "toggle_think", "Reasoning"),
        Binding("ctrl+q", "quit", "Quit"),
        *ThemedApp.THEME_BINDINGS,
    ]

    def __init__(self, make_pane: Callable[[], SessionPane]):
        """make_pane: a new session (its own log, in the room's folder)."""
        super().__init__()
        self.setup_themes()
        self.make_pane = make_pane
        self._count = 0

    # --- layout -----------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static(id="brand")
                yield Static("ROOMS", id="sidebar-title")
                yield VerticalScroll(id="rooms")
                yield NewRoom("+ new room", id="newroom")
            yield Bar("x", apply=self.set_sidebar, id="sidebar-edge")
            yield ContentSwitcher(id="stage")
        with Horizontal(id="bar"):
            yield Static(id="bar-left")
            yield Static(KEYS, id="bar-right")

    async def on_mount(self) -> None:
        await self.action_new_room()
        self.set_interval(0.5, self._refresh_bar)
        self.watch(self, "theme", self._theme_changed, init=False)

    def _theme_changed(self, _old: str, _new: str) -> None:
        self._refresh_bar()

    def set_sidebar(self, x: int) -> None:
        side = self.query_one("#sidebar")
        side.display = True
        side.styles.width = max(12, min(40, x))

    def action_toggle_sidebar(self) -> None:
        side = self.query_one("#sidebar")
        side.display = not side.display
        self.query_one("#sidebar-edge").display = side.display

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
        c = ink(self.current_theme)
        self.query_one("#brand", Static).update(RText.assemble(("◆ ", c.accent), ("diwan", f"bold {c.ink}")))
        for room in self.rooms:
            for p in room.panes:
                title = f" {GRIP} {p.label} "
                if p.border_title != title:
                    p.border_title = title
        t = RText.assemble((f" {self.room.title} ", f"bold {c.bg} on {c.accent}"), "  ")
        for p in self.room.panes:
            m, color = mark(p.state, c)
            t.append(f"{m} ", style=color)
            t.append(f"{p.label}  ", style=f"bold {c.ink}" if p is self.pane else c.dim)
        if self.room.mode == "focus":
            t.append(" layout: focus", style=c.dim)
        self.query_one("#bar-left", Static).update(t)
        for card in self.query(RoomCard):
            card.show(card.room is self.room)

    # --- rooms ------------------------------------------------------------------------------

    async def action_new_room(self) -> None:
        self._count += 1
        room = Room(f"room {self._count}", f"room-{self._count}")
        stage = self.query_one("#stage", ContentSwitcher)
        await stage.mount(room)
        await room.mount(Static("No session here. alt+s starts one.", classes="empty"))
        await self.query_one("#rooms", VerticalScroll).mount(RoomCard(room))
        self.show_room(room)
        await self.action_new_session()

    def show_room(self, room: Room) -> None:
        self.query_one("#stage", ContentSwitcher).current = room.id
        panes = room.panes
        if panes:
            panes[0].prompt.focus()
        room.call_after_refresh(room.relayout)
        self._refresh_bar()

    def action_next_room(self, step: int) -> None:
        rooms = self.rooms
        self.show_room(rooms[(rooms.index(self.room) + step) % len(rooms)])

    # --- sessions ---------------------------------------------------------------------------

    async def action_new_session(self) -> None:
        room = self.room
        try:
            pane = self.make_pane()
        except TarjumanError as e:   # a missing key, an unknown provider
            self.notify(str(e), severity="error")
            return
        here = self.pane
        await room.add(pane, near=here if here in room.panes else None)
        pane.prompt.focus()
        self._refresh_bar()

    async def action_close_session(self) -> None:
        pane = self.pane
        if pane is not None:
            await self._close(pane)

    @on(SessionPane.Closed)
    async def _session_closed(self, event: SessionPane.Closed) -> None:
        await self._close(event.pane)

    async def _close(self, pane: SessionPane) -> None:
        room = pane.parent
        if isinstance(room, Room):
            room.forget(pane)
        await pane.remove()   # its turn stops and waiting approvals answer "no" (on_unmount)
        if isinstance(room, Room):
            room.relayout()
            rest = room.panes
            if rest:
                rest[0].prompt.focus()
        self._refresh_bar()

    def action_zoom(self) -> None:
        room, pane = self.room, self.pane
        if pane is None:
            return
        room.zoomed = None if room.zoomed is pane else pane
        room.relayout()
        pane.prompt.focus()

    def action_layout(self) -> None:
        """F3: "focus" grows the focused session; back to "manual" evens the sizes out."""
        room = self.room
        room.mode = "focus" if room.mode == "manual" else "manual"
        if room.mode == "manual":
            room.even()
        else:
            room.relayout()
        self._refresh_bar()

    def on_descendant_focus(self, _: events.DescendantFocus) -> None:
        room = self.room
        if room.mode == "focus":
            room.relayout()

    def _neighbour(self, direction: str) -> SessionPane | None:
        """The nearest session in that direction, by where they sit on screen."""
        here = self.pane
        if here is None:
            return None
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
        return best

    def action_move(self, direction: str) -> None:
        best = self._neighbour(direction)
        if best is not None:
            best.prompt.focus()

    def action_swap(self, direction: str) -> None:
        here, other = self.pane, self._neighbour(direction)
        if here is not None and other is not None:
            self.room.swap(here, other)
            here.prompt.focus()

    def action_interrupt(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss("no")
            return
        if self.pane is not None:
            self.pane.interrupt()

    def action_toggle_think(self) -> None:
        if self.pane is not None:
            self.pane.action_toggle_think()

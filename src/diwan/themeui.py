"""The theme picker (F2) and the light/dark switch (Ctrl+L), shared by both apps.

The picker is a panel on the right: type to filter, Up/Down previews each theme live on the
whole screen, Enter keeps it, Esc goes back to the one you had. A kept theme becomes the
dark or the light default (by what it is) and is saved in ~/.diwan/ui.json."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import themes
from .themes import Choice, available


class ThemePicker(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ThemePicker { align: right top; background: transparent; }
    ThemePicker #panel { width: 44; height: 100%; background: $surface; padding: 1 1;
                         border-left: tall $panel; }
    ThemePicker .hint { color: $text-muted; height: auto; margin: 0 0 1 0; }
    ThemePicker #filter { margin: 0 0 1 0; }
    ThemePicker #themes { height: 1fr; background: $surface; border: none; }
    """

    def __init__(self, entries: list[tuple[str, str]], current: str):
        super().__init__()
        self.entries, self.start = entries, current

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static("Themes · type to filter (add “dark” or “light”) · ↑↓ preview · "
                         "Enter keeps · Esc goes back", classes="hint")
            yield Input(placeholder="filter…", id="filter")
            yield OptionList(id="themes")

    def on_mount(self) -> None:
        self._fill("")
        self.query_one("#filter", Input).focus()

    def _fill(self, q: str) -> None:
        words = q.lower().split()
        mode = next((w for w in words if w in ("dark", "light")), None)
        words = [w for w in words if w not in ("dark", "light")]
        ol = self.query_one("#themes", OptionList)
        ol.clear_options()
        ol.add_options([Option(label, id=name) for name, label in self.entries
                        if all(w in label.lower() for w in words)
                        and (mode is None or (mode == "light") == label.endswith("(light)"))])

    def on_input_changed(self, ev: Input.Changed) -> None:
        self._fill(ev.value)

    def on_key(self, ev) -> None:
        # the arrows move the list while you type, so filtering and previewing go together
        ol = self.query_one("#themes", OptionList)
        act = {"down": ol.action_cursor_down, "up": ol.action_cursor_up,
               "pagedown": ol.action_page_down, "pageup": ol.action_page_up}.get(ev.key)
        if act is not None:
            act()
            ev.stop()
            ev.prevent_default()
        elif ev.key == "enter" and ol.highlighted is not None:
            ol.action_select()
            ev.stop()
            ev.prevent_default()

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option.id:
            self.app.theme = ev.option.id

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)


class ThemedApp(App):
    """Registers every theme, starts on the saved choice, and adds F2 and Ctrl+L."""

    THEME_BINDINGS: ClassVar = [
        Binding("f2", "pick_theme", "Theme", priority=True),
        Binding("ctrl+l", "light_dark", "Light/dark", priority=True),
    ]

    def setup_themes(self) -> None:
        self._themes = available()
        for t in self._themes:
            self.register_theme(t)
        self.choice = Choice.load()
        self._apply_choice()

    def _dark_wanted(self) -> bool:
        if self.choice.mode == "auto":
            return themes.system_is_dark()
        return self.choice.mode != "light"

    def _apply_choice(self) -> None:
        names = {t.name for t in self._themes}
        dark = self._dark_wanted()
        want = self.choice.dark if dark else self.choice.light
        if want not in names:
            want = "modus-vivendi-tinted" if dark else "modus-operandi-tinted"
        self.theme = want

    def action_light_dark(self) -> None:
        self.choice.mode = "light" if self.current_theme.dark else "dark"
        self._apply_choice()
        self._save_choice()

    def action_pick_theme(self) -> None:
        if isinstance(self.screen, ThemePicker):
            return
        entries = [(t.name, t.name + ("" if t.dark else "  (light)")) for t in self._themes]
        before = self.theme

        def done(name: str | None) -> None:
            names = {t.name for t in self._themes}
            if name not in names:   # Esc (or the app's Esc, which answers "no"): go back
                self.theme = before
                return
            self.theme = name
            # it becomes the default for its kind; a fixed mode of the other kind follows it
            if self.current_theme.dark:
                self.choice.dark = name
                if self.choice.mode == "light":
                    self.choice.mode = "dark"
            else:
                self.choice.light = name
                if self.choice.mode == "dark":
                    self.choice.mode = "light"
            self._save_choice()

        self.push_screen(ThemePicker(entries, before), callback=done)

    def _save_choice(self) -> None:
        try:
            self.choice.save()
        except OSError:
            self.notify("Couldn't save the theme choice to ~/.diwan/ui.json", severity="warning")

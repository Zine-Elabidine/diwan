import json

import pytest
from tarjuman.fake import Fake

from diwan import themes
from diwan.agent import Agent
from diwan.log import Log
from diwan.models import Ref, Router
from diwan.themes import Choice, from_kitty
from diwan.themeui import ThemePicker
from diwan.tools import default_tools
from diwan.tui import DiwanApp

KITTY = """## name: Test Dusk
background #101820
foreground #e0e0e0
color1 #ff0000
color2 #00ff00
color3 #ffff00
color4 #0000ff
color6 #00ffff
color12 #8888ff
"""


def test_a_kitty_theme_becomes_a_complete_textual_theme():
    t = from_kitty(KITTY, "dusk")
    assert t.name == "kitty: Test Dusk" and t.dark
    assert t.primary == "#8888ff" and t.success == "#00ff00"
    assert "dim" in t.variables and "rule" in t.variables   # what Diwan's CSS needs


def test_a_kitty_file_without_colours_is_skipped():
    assert from_kitty("# nothing here", "x") is None


def test_choice_round_trips_and_defaults_to_modus(tmp_path):
    default = Choice()
    assert (default.dark, default.light) == ("modus-vivendi-tinted", "modus-operandi-tinted")
    assert default.mode == "auto"
    Choice("nord", "solarized-light", "dark").save(tmp_path / "ui.json")
    c = Choice.load(tmp_path / "ui.json")
    assert (c.dark, c.light, c.mode) == ("nord", "solarized-light", "dark")


def test_drop_in_themes_are_found(tmp_path, monkeypatch):
    monkeypatch.setattr(themes, "KITTY_ZIP", tmp_path / "none.zip")
    (themes.home() / "themes").mkdir(parents=True)
    (themes.home() / "themes" / "dusk.conf").write_text(KITTY)
    names = [t.name for t in themes.available()]
    assert names[:2] == ["modus-vivendi-tinted", "modus-operandi-tinted"] and "kitty: Test Dusk" in names


def make_app(tmp_path):
    return DiwanApp(lambda lg, r: Agent(Fake([]), "m", lg, default_tools(), "sys"),
                    Log.new(cwd=str(tmp_path)), tmp_path, Router("openrouter"), Ref("openrouter", "m"))


@pytest.mark.asyncio
async def test_light_dark_switch_and_the_picker(tmp_path, monkeypatch):
    monkeypatch.setattr(themes, "system_is_dark", lambda: True)
    monkeypatch.setattr(themes, "KITTY_ZIP", tmp_path / "none.zip")
    app = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause(0.1)
        assert app.theme == "modus-vivendi-tinted"          # dark default
        await pilot.press("ctrl+l")
        assert app.theme == "modus-operandi-tinted"         # light default
        await pilot.press("ctrl+l")
        assert app.theme == "modus-vivendi-tinted"

        await pilot.press("f2")
        assert isinstance(app.screen, ThemePicker)
        await pilot.press(*"nord", "down")
        assert app.theme == "nord"                          # previewed live
        await pilot.press("escape")
        assert app.theme == "modus-vivendi-tinted"          # Esc goes back

        await pilot.press("f2")
        await pilot.press(*"nord", "down", "enter")
        assert app.theme == "nord" and not isinstance(app.screen, ThemePicker)
    saved = json.loads((themes.home() / "ui.json").read_text())
    assert saved["dark"] == "nord"

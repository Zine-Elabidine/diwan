"""Which themes Diwan can show, which one is on, and remembering the choice.

Diwan ships its own theme and the two Modus themes (dark and light defaults), plus Textual's
built-ins. It also reads, at run time, kitty's theme collection when kitty has downloaded it
(~/.cache/kitty/kitty-themes.zip) and any kitty `.conf` dropped in ~/.diwan/themes/. Those
are not bundled: they stay where they are, under their own licences.

The choice lives in ~/.diwan/ui.json: a dark theme, a light theme, and a mode ("auto"
follows the desktop's light/dark setting, or "dark" / "light")."""

from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from textual.theme import BUILTIN_THEMES, Theme

from .theme import DIWAN, MODUS_DARK, MODUS_LIGHT, complete, mix

KITTY_ZIP = Path.home() / ".cache" / "kitty" / "kitty-themes.zip"


def home() -> Path:
    return Path(os.environ.get("DIWAN_HOME", Path.home() / ".diwan"))


@dataclass
class Choice:
    dark: str = MODUS_DARK.name
    light: str = MODUS_LIGHT.name
    mode: str = "auto"   # "auto", "dark" or "light"

    @classmethod
    def load(cls, path: Path | None = None) -> Choice:
        path = path or home() / "ui.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        return cls(**{k: str(v) for k, v in data.items() if k in ("dark", "light", "mode")})

    def save(self, path: Path | None = None) -> None:
        path = path or home() / "ui.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"dark": self.dark, "light": self.light, "mode": self.mode}, indent=1))


def system_is_dark() -> bool:
    """The desktop's light/dark setting (GNOME), dark when it can't be read."""
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                             capture_output=True, text=True, timeout=1, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return "light" not in out


def from_kitty(text: str, name: str) -> Theme | None:
    """A Textual theme from a kitty theme file (background, foreground, color0-15)."""
    kv: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"##\s*name:\s*(.+)", line)
        if m:
            name = m.group(1).strip()
        m = re.match(r"^(\w+)\s+(#[0-9a-fA-F]{6})\b", line)
        if m:
            kv[m.group(1)] = m.group(2).lower()
    need = ("background", "foreground", "color1", "color2", "color3", "color4", "color6")
    if not all(k in kv for k in need):
        return None
    bg, fg = kv["background"], kv["foreground"]
    blue = kv.get("color12", kv["color4"])
    lum = sum(w * int(bg[i:i + 2], 16) for w, i in ((0.2126, 1), (0.7152, 3), (0.0722, 5))) / 255
    return complete(Theme(
        name=f"kitty: {name}", dark=lum < 0.5, foreground=fg, background=bg,
        surface=mix(bg, fg, 0.05), panel=mix(bg, fg, 0.12), boost=mix(bg, fg, 0.08),
        primary=blue, secondary=kv["color6"], accent=blue, success=kv["color2"],
        warning=kv["color3"], error=kv["color1"]))


def _kitty_files() -> Iterable[tuple[str, str]]:
    if KITTY_ZIP.is_file():
        try:
            with zipfile.ZipFile(KITTY_ZIP) as z:
                for n in sorted(z.namelist()):
                    if "/themes/" in n and n.endswith(".conf"):
                        yield Path(n).stem, z.read(n).decode("utf-8", "ignore")
        except (OSError, zipfile.BadZipFile):
            pass
    for f in sorted((home() / "themes").glob("*.conf")):
        try:
            yield f.stem, f.read_text(errors="ignore")
        except OSError:
            continue


def available() -> list[Theme]:
    """Every theme Diwan can show: its own first, then Textual's, then kitty's."""
    out = [MODUS_DARK, MODUS_LIGHT, complete(DIWAN)]
    out += [complete(BUILTIN_THEMES[n]) for n in sorted(BUILTIN_THEMES)]
    seen = {t.name for t in out}
    for stem, text in _kitty_files():
        t = from_kitty(text, stem)
        if t is not None and t.name not in seen:
            seen.add(t.name)
            out.append(t)
    return out

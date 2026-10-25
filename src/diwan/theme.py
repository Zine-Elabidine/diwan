"""Diwan's colors, from its README banner: warm ink on near-black, amber for focus, neon
cyan / green / pink for states."""

from __future__ import annotations

from dataclasses import dataclass, replace

from textual.color import Color
from textual.theme import Theme

BG, CHIP, RULE = "#0c0b11", "#17151f", "#24212e"
INK, DIM = "#f4ecd8", "#8c879c"
AMBER, CYAN, GREEN, PINK, ORANGE = "#f2a541", "#5cc8ff", "#48f07a", "#ff4fa3", "#ff6a2b"

DIWAN = Theme(
    name="diwan", primary=AMBER, secondary=CYAN, accent=AMBER, warning=AMBER, error=PINK,
    success=GREEN, foreground=INK, background=BG, surface=CHIP, panel=CHIP, boost="#1d1a27",
    dark=True,
    variables={"rule": RULE, "dim": DIM, "cyan": CYAN, "pink": PINK, "orange": ORANGE,
               "block-cursor-background": AMBER, "block-cursor-foreground": BG,
               "input-selection-background": "#f2a54140", "footer-key-foreground": AMBER,
               "scrollbar": RULE, "scrollbar-hover": DIM, "scrollbar-background": BG,
               "border": RULE, "border-blurred": RULE},
)


def mix(a: str | None, b: str | None, t: float) -> str:
    """The colour `t` of the way from `a` to `b`, as #rrggbb (for Rich styles, which can't
    use the theme's CSS variables)."""
    if not a or not b:
        return a or b or "#808080"
    return Color.parse(a).blend(Color.parse(b), t).hex6


def complete(theme: Theme) -> Theme:
    """`theme` with the extra variables Diwan's CSS uses ($dim, $rule, …), derived from its own
    colours when it doesn't set them, so any theme (Textual's, kitty's) can style Diwan."""
    fg, bg = theme.foreground or "#e0e0e0", theme.background or "#121212"
    extra = {"dim": mix(fg, bg, 0.45), "rule": mix(bg, fg, 0.16),
             "scrollbar": mix(bg, fg, 0.16), "scrollbar-hover": mix(fg, bg, 0.45),
             "scrollbar-background": bg, "border": mix(bg, fg, 0.16), "border-blurred": mix(bg, fg, 0.16)}
    return replace(theme, variables={**extra, **(theme.variables or {})})


def _modus(name: str, fg: str, bg: str, red: str, green: str, yellow: str, blue: str, cyan: str,
           dark: bool) -> Theme:
    return complete(Theme(
        name=name, dark=dark, foreground=fg, background=bg,
        surface=mix(bg, fg, 0.05), panel=mix(bg, fg, 0.12), boost=mix(bg, fg, 0.08),
        primary=blue, secondary=cyan, accent=blue, success=green, warning=yellow, error=red))


# Modus themes by Protesilaos Stavrou (https://protesilaos.com/emacs/modus-themes), the tinted
# variants: built for the highest contrast (WCAG AAA). Only their colour values are used here.
MODUS_DARK = _modus("modus-vivendi-tinted", "#ffffff", "#0d0e1c", "#ff5f59", "#44bc44", "#d0bc00",
                    "#79a8ff", "#00d3d0", dark=True)
MODUS_LIGHT = _modus("modus-operandi-tinted", "#000000", "#fbf7f0", "#a60000", "#006800", "#6f5500",
                     "#3548cf", "#005e8b", dark=False)


@dataclass
class Ink:
    """A theme's colours as plain #hex strings, for the Rich styles the UI builds in code."""
    ink: str
    dim: str
    bg: str
    accent: str
    secondary: str
    success: str
    warning: str
    error: str


def ink(theme: Theme) -> Ink:
    fg, bg = theme.foreground or "#e0e0e0", theme.background or "#121212"
    v = theme.variables or {}
    return Ink(ink=fg, dim=v.get("dim") or mix(fg, bg, 0.45), bg=bg,
               accent=theme.accent or theme.primary, secondary=theme.secondary or theme.primary,
               success=theme.success or "#44bc44", warning=theme.warning or "#d0bc00",
               error=theme.error or "#ff5f59")

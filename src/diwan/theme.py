"""Diwan's colors, from its README banner: warm ink on near-black, amber for focus, neon
cyan / green / pink for states."""

from __future__ import annotations

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

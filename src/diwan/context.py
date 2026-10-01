"""How full the context is, counted in a model's own tokens."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextUse:
    """How full the context is, in the current model's own tokens."""
    used: int             # tokens the next request will hold
    window: int | None    # the model's context window, if known
    usable: int | None    # the window minus the room kept for the answer
    exact: bool           # True right after a reply from this model; otherwise partly estimated

    @property
    def fraction(self) -> float | None:
        return self.used / self.usable if self.usable else None

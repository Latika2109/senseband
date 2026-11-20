"""Free-fall -> impact -> lying still = fall (ported from edge/prototype/processing.py)."""
from __future__ import annotations

from ..config import Fall


class FallDetector:
    def __init__(self, cfg: Fall):
        self.c = cfg
        self.ff_t: float | None = None
        self.impact_t: float | None = None
        self.moving = self.count = 0

    @property
    def trigger_t(self) -> float | None:
        return self.impact_t

    def cancel(self) -> None:
        """A model (or the user) says this trigger was not a fall."""
        self.impact_t = self.ff_t = None

    def update(self, amag: float, t: float) -> str | None:
        """Feed |acc| (g) with its timestamp. Returns 'fall', 'impact' or None."""
        c = self.c
        if amag < c.freefall_g:
            self.ff_t = t
        recent_ff = self.ff_t is not None and t - self.ff_t < 1.0
        if self.impact_t is None:
            if (amag > c.impact_g and recent_ff) or amag > c.hard_impact_g:
                self.impact_t, self.moving, self.count = t, 0, 0
            return None
        dt = t - self.impact_t
        if dt > c.still_after_s:
            self.count += 1
            if abs(amag - 1.0) > c.moving_dev_g:
                self.moving += 1
        if dt > c.still_s:
            still = self.count > 0 and self.moving / self.count < 0.1
            self.impact_t = self.ff_t = None
            return "fall" if still else "impact"
        return None

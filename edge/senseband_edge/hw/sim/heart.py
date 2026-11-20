"""Beat schedule shared by ECG and PPG so both sensors agree on the heart rhythm."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .scenario import Timeline


@dataclass(frozen=True)
class Beat:
    t: float      # R-peak time (s)
    rr: float     # interval since the previous beat (s)
    af: bool


class BeatSchedule:
    def __init__(self, timeline: Timeline, cfg: dict[str, Any], rng: np.random.Generator):
        self.tl, self.h, self.rng = timeline, cfg["heart"], rng
        self.beats: list[Beat] = []
        self._next, self._prev = 0.3, 0.3

    def _extend(self, t_end: float) -> None:
        h = self.h
        while self._next <= t_end:
            tb = self._next
            hr = float(self.tl.num("hr", tb)[0])
            if hr < h["min_hr_for_beats"]:        # asystole: no beats, keep checking
                self._next = tb + 0.25
                continue
            af = self.tl.step("rhythm", tb) == "af"
            self.beats.append(Beat(tb, tb - self._prev, af))
            self._prev = tb
            mean_rr = 60.0 / hr
            if af:
                k = 1.0 + self.rng.normal(0.0, h["af_rr_cv"])
            else:
                k = (1.0 + h["rsa_amp"] * np.sin(2 * np.pi * h["resp_hz"] * tb)
                     + self.rng.normal(0.0, h["hrv_sd"]))
            self._next = tb + mean_rr * float(np.clip(k, 0.45, 1.7))

    def between(self, a: float, b: float) -> list[Beat]:
        self._extend(b)
        return [x for x in self.beats if a <= x.t <= b]

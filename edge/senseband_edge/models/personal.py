"""Per-user correction of the population expected-HR model (docs/04, M5): recursive least squares.

corrected = c0 + c1 * z   with z = (population_prediction - 100) / 50, so the starting point (c0=100, c1=50)
is the identity. RLS with a forgetting factor learns THIS person's resting level and slope during the first
48 h (the "learning period") and keeps adapting slowly. Pure Python: runs on a Pi Zero.
"""
from __future__ import annotations

from typing import Any


class PersonalHr:
    SLOPE_RANGE = (25.0, 80.0)        # c1: 0.5x .. 1.6x of the population slope
    MAX_OFFSET = 40.0                 # c0 may move at most 40 bpm from 100

    def __init__(self, forgetting: float = 0.999, var_offset: float = 400.0, var_slope: float = 100.0):
        self.lam = forgetting
        self.c = [100.0, 50.0]
        self.p = [[var_offset, 0.0], [0.0, var_slope]]
        self.n = 0

    @staticmethod
    def _z(pred: float) -> float:
        return (pred - 100.0) / 50.0

    def correct(self, pred: float) -> float:
        return self.c[0] + self.c[1] * self._z(pred)

    def update(self, pred: float, actual: float) -> None:
        """Feed one (population prediction, measured HR) pair from a calm, good-quality window."""
        x = [1.0, self._z(pred)]
        px = [self.p[0][0] * x[0] + self.p[0][1] * x[1], self.p[1][0] * x[0] + self.p[1][1] * x[1]]
        denom = self.lam + x[0] * px[0] + x[1] * px[1]
        k = [px[0] / denom, px[1] / denom]
        err = actual - (self.c[0] * x[0] + self.c[1] * x[1])
        self.c[0] += k[0] * err
        self.c[1] += k[1] * err
        xp = [x[0] * self.p[0][0] + x[1] * self.p[1][0], x[0] * self.p[0][1] + x[1] * self.p[1][1]]
        for i in range(2):
            for j in range(2):
                self.p[i][j] = (self.p[i][j] - k[i] * xp[j]) / self.lam
        self.c[1] = min(max(self.c[1], self.SLOPE_RANGE[0]), self.SLOPE_RANGE[1])
        self.c[0] = min(max(self.c[0], 100.0 - self.MAX_OFFSET), 100.0 + self.MAX_OFFSET)
        self.n += 1

    def state(self) -> dict[str, Any]:
        return {"c": list(self.c), "p": [list(r) for r in self.p], "n": self.n, "lam": self.lam}

    @classmethod
    def from_state(cls, s: dict[str, Any]) -> PersonalHr:
        o = cls(float(s.get("lam", 0.999)))
        o.c, o.p, o.n = [float(v) for v in s["c"]], [[float(v) for v in r] for r in s["p"]], int(s["n"])
        return o

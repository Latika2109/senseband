"""Repetition counter with hysteresis, tempo and hold measurement (model M7, no training needed)."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


def nominal_duration(up_s: float, down_s: float, down_frac: float) -> float:
    """How long a perfectly paced rep is between the counter's start/end thresholds.

    A rep is measured from the angle rising past `down_frac` of the target to falling back below it. For a smooth
    (cosine) movement taking `up_s` up and `down_s` down that is (up+down) * (1 - acos(1 - 2*down_frac) / pi), about
    70 % of the full cycle for the default 20 %. "Too fast" must be judged against THIS, not the raw tempo."""
    return (up_s + down_s) * (1.0 - math.acos(1.0 - 2.0 * down_frac) / math.pi)


@dataclass
class Rep:
    n: int
    peak_deg: float
    duration_s: float
    up_s: float
    down_s: float
    hold_s: float
    too_fast: bool
    over_limit: bool
    t_end: float


@dataclass
class RepCounter:
    target_deg: float
    up_frac: float = 0.5
    down_frac: float = 0.2
    in_target_frac: float = 0.9
    min_rep_s: float = 0.0            # shorter reps are "too fast"
    max_speed_dps: float = 1e9
    limit_deg: float | None = None    # doctor's maximum angle
    reps: list[Rep] = field(default_factory=list)
    max_angle: float = 0.0
    speed_violations: int = 0
    over_limit_now: bool = False
    in_target: bool = False
    _phase: str = "idle"              # idle | in_rep
    _t0: float = 0.0
    _t_peak: float = 0.0
    _peak: float = 0.0
    _hold: float = 0.0
    _last: tuple[float, float] | None = None
    _recent: deque = field(default_factory=lambda: deque(maxlen=64))   # (t, angle) over ~0.25 s for a stable speed
    speed_window_s: float = 0.25
    _fast_flag: bool = False
    _over: bool = False

    @property
    def count(self) -> int:
        return len(self.reps)

    def update(self, angle: float, t: float) -> Rep | None:
        up, down = self.up_frac * self.target_deg, self.down_frac * self.target_deg
        zone = self.in_target_frac * self.target_deg
        speed = 0.0
        dt = (t - self._last[1]) if (self._last is not None and t > self._last[1]) else 0.0
        self._recent.append((t, angle))
        while self._recent and t - self._recent[0][0] > self.speed_window_s:
            self._recent.popleft()
        if len(self._recent) >= 2 and t - self._recent[0][0] >= 0.15:     # speed over ~0.2 s: sensor jitter averages out
            speed = abs(angle - self._recent[0][1]) / (t - self._recent[0][0])
        self.max_angle = max(self.max_angle, angle)
        self.in_target = angle >= zone
        self.over_limit_now = self.limit_deg is not None and angle > self.limit_deg
        if self.over_limit_now:
            self._over = True
        if speed > self.max_speed_dps and self._phase == "in_rep":
            self._fast_flag = True
        self._last = (angle, t)
        done: Rep | None = None
        if self._phase == "idle":
            if angle > down:
                self._phase, self._t0, self._t_peak, self._peak = "in_rep", t, t, angle
                self._hold, self._fast_flag, self._over = 0.0, False, self.over_limit_now
        else:
            if angle > self._peak:
                self._peak, self._t_peak = angle, t
            if angle >= zone:
                self._hold += dt
            if angle < down:
                if self._peak >= up:                       # a real repetition
                    dur = t - self._t0
                    fast = self._fast_flag or dur < self.min_rep_s
                    if fast:
                        self.speed_violations += 1
                    done = Rep(len(self.reps) + 1, round(self._peak, 1), round(dur, 2),
                               round(self._t_peak - self._t0, 2), round(t - self._t_peak, 2),
                               round(self._hold, 2), fast, self._over, t)
                    self.reps.append(done)
                self._phase = "idle"
        return done

    def summary(self) -> dict:
        n = self.count
        peaks = [r.peak_deg for r in self.reps]
        hit = sum(1 for p in peaks if p >= self.in_target_frac * self.target_deg)
        return {"reps": n, "avg_peak_angle": round(sum(peaks) / n, 1) if n else None,
                "max_angle": round(self.max_angle, 1),
                "target_hit_pct": round(100.0 * hit / n, 1) if n else None,
                "too_fast_count": sum(1 for r in self.reps if r.too_fast),
                "over_limit_count": sum(1 for r in self.reps if r.over_limit)}

"""Small-band text protocol parser (docs/05: Q, V, B; legacy I) and state."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Quality


@dataclass
class SmallBandState:
    connected_ever: bool = False
    last_seen: float | None = None
    quat: tuple[float, float, float, float] | None = None
    quat_t: float | None = None
    hr: float | None = None
    spo2: float | None = None
    hr_valid: bool = False
    spo2_valid: bool = False
    finger: bool = False
    perfusion: float | None = None
    battery_pct: float | None = None
    vitals_t: float | None = None
    pitch_roll: tuple[float, float] | None = None
    bad_lines: int = 0
    _hr_smooth: float | None = field(default=None, repr=False)

    def fresh(self, now: float, max_age: float = 5.0) -> bool:
        return self.last_seen is not None and now - self.last_seen <= max_age


class SmallBandParser:
    def __init__(self, q: Quality):
        self.q = q
        self.state = SmallBandState()

    def feed(self, line: str, now: float) -> str | None:
        """Parse one line; returns the message type ('Q','V','B','I') or None if malformed."""
        s, p = self.state, line.strip().split(",")
        try:
            kind = p[0]
            if kind == "Q" and len(p) == 6:
                s.quat, s.quat_t = tuple(float(v) for v in p[2:6]), now  # type: ignore[assignment]
            elif kind == "V" and len(p) in (6, 7):
                hr, hrv, sp, spv, fing = (int(v) for v in p[1:6])
                s.perfusion = float(p[6]) if len(p) == 7 else None
                s.finger, s.vitals_t = bool(fing), now
                lo, hi = self.q.ppg_hr_range
                s.hr_valid = bool(fing and hrv and lo < hr < hi)
                if s.hr_valid:
                    s._hr_smooth = hr if s._hr_smooth is None else 0.7 * s._hr_smooth + 0.3 * hr
                    s.hr = round(s._hr_smooth, 1)
                else:
                    s.hr = None
                    if not fing:
                        s._hr_smooth = None
                lo, hi = self.q.ppg_spo2_range
                s.spo2_valid = bool(fing and spv and lo <= sp <= hi)
                s.spo2 = float(sp) if s.spo2_valid else None
            elif kind == "B" and len(p) == 2:
                s.battery_pct = float(p[1])
            elif kind == "I" and len(p) == 4:
                s.pitch_roll = (float(p[2]), float(p[3]))
            else:
                s.bad_lines += 1
                return None
        except ValueError:
            s.bad_lines += 1
            return None
        s.last_seen, s.connected_ever = now, True
        return kind

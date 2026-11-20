"""Features for the personal expected-heart-rate model (M5)."""
from __future__ import annotations

import numpy as np

ACTIVITY_CODES = {"lying": 0, "sleeping": 0, "sitting": 1, "standing": 2, "walking": 3, "stairs": 4,
                  "exercising": 5, "running": 6, "unknown": 2}

EXPECTED_HR_FEATURES = ["activity", "intensity", "cadence_hz", "load_5min", "tod_sin", "tod_cos", "temp_c", "beta_blocker"]


def expected_hr_features(activity: str, intensity: float, cadence_hz: float, load_5min: float,
                         hour_of_day: float, temp_c: float | None = None, beta_blocker: bool = False) -> np.ndarray:
    """intensity 0..1 (movement), load_5min = mean intensity over the last 5 minutes (exertion history)."""
    ang = 2 * np.pi * (hour_of_day % 24.0) / 24.0
    v = [ACTIVITY_CODES.get(activity, 2), float(intensity), float(cadence_hz), float(load_5min),
         float(np.sin(ang)), float(np.cos(ang)), 36.5 if temp_c is None else float(temp_c), 1.0 if beta_blocker else 0.0]
    return np.asarray(v, dtype=np.float32)

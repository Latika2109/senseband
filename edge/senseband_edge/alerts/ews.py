"""Early-warning score (NEWS2 subset, model M10 fallback): HR, SpO2, temperature."""
from __future__ import annotations

from ..config import Ews


def _score(table: list[list[float]], v: float | None) -> int:
    if v is None:
        return 0
    for bound, pts in table:
        if v < bound:
            return int(pts)
    return int(table[-1][1])


def ews_score(cfg: Ews, hr: float | None, spo2: float | None, temp: float | None) -> int:
    """Higher = sicker. Missing (poor-quality) inputs score 0 rather than guessing."""
    return _score(cfg.hr, hr) + _score(cfg.spo2, spo2) + _score(cfg.temp, temp)

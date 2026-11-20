"""Recovery forecast (model M8): saturating exponential ROM(t) = Rinf - (Rinf - R0) * exp(-k t)
fitted to a patient's own sessions, with a bootstrap confidence interval. An ESTIMATE only."""
from __future__ import annotations

import warnings
from typing import Any

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit


def _model(t: np.ndarray, r_inf: float, r0: float, k: float) -> np.ndarray:
    return r_inf - (r_inf - r0) * np.exp(-k * t)


def _fit(t: np.ndarray, y: np.ndarray, cap: float) -> tuple[float, float, float] | None:
    p0 = (min(cap, y.max() * 1.2), y[0], 0.05)
    bounds = ([y.max() * 0.95, 0.0, 1e-4], [cap * 1.05, y.max() + 1e-6, 2.0])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            p, _ = curve_fit(_model, t, y, p0=p0, bounds=bounds, maxfev=4000)
        return float(p[0]), float(p[1]), float(p[2])
    except (RuntimeError, ValueError):
        return None


def _days_to(target: float, r_inf: float, r0: float, k: float) -> float | None:
    if target <= r0:
        return 0.0
    if target >= r_inf * 0.995:
        return None                                   # never reached on this trend
    return float(-np.log((r_inf - target) / (r_inf - r0)) / k)


def forecast(days: list[float], rom: list[float], target: float, normal: float,
             min_sessions: int = 5, n_boot: int = 200, seed: int = 0) -> dict[str, Any]:
    """days: days since the first session; rom: degrees. Returns params + date (days from start)."""
    out: dict[str, Any] = {"ok": False, "n": len(days), "target": target}
    if len(days) < min_sessions or len(set(days)) < 3:
        out["reason"] = "not_enough_sessions"
        return out
    t, y = np.asarray(days, float), np.asarray(rom, float)
    order = np.argsort(t)
    t, y = t[order], y[order]
    p = _fit(t, y, normal)
    if p is None:
        out["reason"] = "fit_failed"
        return out
    r_inf, r0, k = p
    resid = y - _model(t, *p)
    rng = np.random.default_rng(seed)
    dates, rinfs = [], []
    for _ in range(n_boot):
        yb = np.clip(_model(t, *p) + rng.choice(resid, len(resid)), 0, None)
        pb = _fit(t, yb, normal)
        if pb:
            rinfs.append(pb[0])
            d = _days_to(target, *pb)
            if d is not None:
                dates.append(d)
    est = _days_to(target, r_inf, r0, k)
    out.update(ok=True, params={"r_inf": r_inf, "r0": r0, "k": k}, est_days=est,
               plateau_deg=r_inf, rmse=float(np.sqrt(np.mean(resid ** 2))))
    if est is None:
        out["reason"] = "target_not_reachable_on_trend"
    if len(dates) >= max(10, n_boot // 4):
        lo, hi = np.percentile(dates, [10, 90])
        out.update(ci_low_days=float(lo), ci_high_days=float(hi),
                   confidence="low" if (hi - lo) > 0.75 * max(est or 1.0, 1.0) else "ok")
    else:
        out["confidence"] = "low"
    out["note"] = "estimate only"
    return out

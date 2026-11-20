"""Agreement statistics for device-vs-reference studies (Bland-Altman, ARMS, tolerance rates) and acceptance verdicts."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Acceptance criteria. Sources: docs/06 M3/M7; ISO 80601-2-61 style SpO2 ARMS <= 4 % over 70-100 %; HR +-5 bpm.
CRITERIA: dict[str, dict[str, float]] = {
    "spo2": {"arms_max": 4.0, "tol": 3.0, "within_tol_min_pct": 90.0, "min_range": 0.0},
    "hr": {"mae_max": 5.0, "tol": 5.0, "within_tol_min_pct": 90.0},
    "angle": {"max_abs_error": 5.0, "tol": 5.0, "within_tol_min_pct": 95.0},
    "temp": {"mae_max": 0.5, "tol": 0.5, "within_tol_min_pct": 90.0},
}
MIN_SUBJECTS = 5            # docs/06: 5-10 volunteers


@dataclass
class Pair:
    t: float
    device: float
    reference: float
    subject: str = "s1"
    condition: str = ""      # e.g. "rest", "post-exercise", "elbow 45"


def pair_by_time(device: list[tuple[float, float]], reference: list[tuple[float, float]], tol_s: float = 30.0,
                 subject: str = "s1", condition: str = "") -> list[Pair]:
    """Match each reference reading to the nearest device reading within tol_s seconds (each device sample used once)."""
    dev = sorted(device)
    out: list[Pair] = []
    used: set[int] = set()
    times = np.array([d[0] for d in dev]) if dev else np.array([])
    for t, ref in sorted(reference):
        if not len(times):
            break
        order = np.argsort(np.abs(times - t))
        for i in order[:3]:
            if abs(times[i] - t) <= tol_s and int(i) not in used:
                used.add(int(i))
                out.append(Pair(t, dev[int(i)][1], ref, subject, condition))
                break
    return out


def agreement(pairs: list[Pair], tol: float) -> dict[str, Any]:
    """Bland-Altman bias and 95 % limits of agreement, MAE, RMSE (= ARMS), Pearson r, share within +-tol."""
    if len(pairs) < 3:
        return {"n": len(pairs), "ok": False, "reason": "fewer than 3 pairs"}
    d = np.array([p.device for p in pairs], dtype=float)
    r = np.array([p.reference for p in pairs], dtype=float)
    diff = d - r
    sd = float(diff.std(ddof=1))
    corr = float(np.corrcoef(d, r)[0, 1]) if d.std() > 0 and r.std() > 0 else float("nan")
    return {"n": len(pairs), "ok": True, "bias": float(diff.mean()), "sd": sd,
            "loa_low": float(diff.mean() - 1.96 * sd), "loa_high": float(diff.mean() + 1.96 * sd),
            "mae": float(np.abs(diff).mean()), "rmse": float(math.sqrt(float((diff ** 2).mean()))),
            "arms": float(math.sqrt(float((diff ** 2).mean()))), "max_abs_error": float(np.abs(diff).max()),
            "r": corr, "within_tol_pct": float(100.0 * np.mean(np.abs(diff) <= tol)), "tol": tol,
            "ref_min": float(r.min()), "ref_max": float(r.max())}


@dataclass
class Verdict:
    kind: str
    passed: bool
    stats: dict[str, Any]
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def evaluate(kind: str, pairs: list[Pair], min_subjects: int = MIN_SUBJECTS) -> Verdict:
    """Pass/fail against CRITERIA, with caveats the report must show (few subjects, narrow range, per-subject spread)."""
    c = CRITERIA[kind]
    st = agreement(pairs, c["tol"])
    if not st["ok"]:
        return Verdict(kind, False, st, ["not enough data"])
    reasons, caveats = [], []
    if "arms_max" in c and st["arms"] > c["arms_max"]:
        reasons.append(f"ARMS {st['arms']:.2f} > {c['arms_max']}")
    if "mae_max" in c and st["mae"] > c["mae_max"]:
        reasons.append(f"MAE {st['mae']:.2f} > {c['mae_max']}")
    if "max_abs_error" in c and st["max_abs_error"] > c["max_abs_error"]:
        reasons.append(f"max error {st['max_abs_error']:.2f} > {c['max_abs_error']}")
    if st["within_tol_pct"] < c["within_tol_min_pct"]:
        reasons.append(f"only {st['within_tol_pct']:.0f}% within +-{c['tol']} (need {c['within_tol_min_pct']:.0f}%)")
    subjects = {p.subject for p in pairs}
    if len(subjects) < min_subjects:
        caveats.append(f"only {len(subjects)} subjects (protocol asks for {min_subjects}-10): result is indicative only")
    if kind == "spo2" and st["ref_min"] > 94:
        caveats.append("reference SpO2 never fell below 94 %: accuracy at low saturation is NOT demonstrated "
                       "(healthy volunteers cannot ethically be desaturated)")
    if kind == "hr" and st["ref_max"] - st["ref_min"] < 40:
        caveats.append("heart-rate range under 40 bpm: include rest and post-exercise readings")
    if kind == "angle" and len({p.condition for p in pairs}) < 3:
        caveats.append("fewer than 3 reference angles (expected 0, 45 and 90 degrees)")
    per = {s: agreement([p for p in pairs if p.subject == s], c["tol"]) for s in sorted(subjects)}
    worst = max((v["mae"] for v in per.values() if v.get("ok")), default=0.0)
    st["per_subject_mae"] = {s: round(v["mae"], 2) for s, v in per.items() if v.get("ok")}
    if len(per) > 1 and worst > 2 * st["mae"] + 1:
        caveats.append(f"one subject is much worse (MAE {worst:.1f}): check sensor placement and skin tone / perfusion")
    return Verdict(kind, not reasons, st, reasons, caveats)


# ------------------------------------------------------------------------------------------ false alarms
def false_alarm_rate(events: list[dict[str, Any]], hours_observed: float,
                     exclude_kinds: tuple[str, ...] = ()) -> dict[str, Any]:
    """Alerts per 24 h while the person was doing normal things (no deliberate test). Cancelled alerts count: the
    person was disturbed. Target (docs/04 M2): < 1 false fall alarm per day."""
    if hours_observed <= 0:
        raise ValueError("hours_observed must be positive")
    kept = [e for e in events if e["kind"] not in exclude_kinds]
    per_day = 24.0 / hours_observed
    out: dict[str, Any] = {"hours": hours_observed, "total": len(kept)}
    for lvl in ("critical", "warning", "info"):
        n = sum(1 for e in kept if e["level"] == lvl)
        out[lvl] = {"count": n, "per_24h": round(n * per_day, 2)}
    by_kind: dict[str, int] = {}
    for e in kept:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    out["by_kind"] = by_kind
    out["sms_triggered"] = sum(1 for e in kept if e.get("status") in ("sms_sent", "sms_failed"))
    out["cancelled_by_user"] = sum(1 for e in kept if e.get("status") == "cancelled")
    out["falls_per_24h"] = round(sum(1 for e in kept if e["kind"] == "fall") * per_day, 2)
    return out


# ------------------------------------------------------------------------------------------ battery
def battery_runtime(samples: list[tuple[float, float]], target_h: float = 24.0, full: float = 100.0,
                    empty: float = 0.0) -> dict[str, Any]:
    """samples: (seconds, percent). Linear discharge fit -> projected hours from `full` to `empty`."""
    if len(samples) < 5:
        return {"ok": False, "reason": "need at least 5 readings"}
    t = np.array([s[0] for s in samples], dtype=float) / 3600.0
    p = np.array([s[1] for s in samples], dtype=float)
    if t.max() - t.min() < 0.5:
        return {"ok": False, "reason": "log at least 30 minutes"}
    slope, icpt = np.polyfit(t, p, 1)
    if slope >= -1e-6:
        return {"ok": False, "reason": "battery is not discharging"}
    fit = slope * t + icpt
    ss_res, ss_tot = float(((p - fit) ** 2).sum()), float(((p - p.mean()) ** 2).sum())
    hours = (full - empty) / -slope
    return {"ok": True, "pct_per_hour": round(float(-slope), 3), "projected_hours": round(float(hours), 1),
            "r2": round(1 - ss_res / ss_tot, 4) if ss_tot > 0 else 1.0, "observed_hours": round(float(t.max() - t.min()), 2),
            "meets_target": bool(hours >= target_h), "target_h": target_h,
            "note": "linear extrapolation: LiPo/18650 curves are not linear near empty, so confirm with a full discharge"}


def power_budget_hours(items_ma: dict[str, float], battery_mah: float, voltage_ratio: float = 1.0) -> float:
    """docs/03 power table as code: hours = capacity / total average current (after boost-converter ratio)."""
    total = sum(items_ma.values()) * voltage_ratio
    return battery_mah / total if total > 0 else float("inf")

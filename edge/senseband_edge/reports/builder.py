"""Aggregate a day or a whole program into a Report (pure data; pdf.py draws it).

Nothing here diagnoses: it states measured values, quality, alerts raised and what was done about them.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import Settings
from ..rehab.service import RecoveryService
from ..storage.db import Store

RESTING = {"lying", "sleeping", "sitting", "standing"}
LEVEL_RANK = {"critical": 3, "warning": 2, "info": 1}
MAX_STRIPS = 3
STRIP_POINTS = 1500


@dataclass
class Report:
    kind: str                                   # "daily" | "program"
    patient: dict[str, Any]
    since_ms: int
    until_ms: int
    generated_ms: int
    program: dict[str, Any] | None
    vitals: dict[str, Any] = field(default_factory=dict)
    activity_minutes: dict[str, int] = field(default_factory=dict)
    sleep: dict[str, Any] = field(default_factory=dict)
    hr_series: list[tuple[int, float | None, float | None]] = field(default_factory=list)
    spo2_series: list[tuple[int, float]] = field(default_factory=list)
    hourly: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    event_counts: dict[str, int] = field(default_factory=dict)
    strips: list[dict[str, Any]] = field(default_factory=list)
    rehab: dict[str, Any] | None = None
    notes: list[dict[str, Any]] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["hr_series"] = [list(x) for x in self.hr_series]
        d["spo2_series"] = [list(x) for x in self.spo2_series]
        return d


def _mean(xs: list[float]) -> float | None:
    return round(float(np.mean(xs)), 1) if xs else None


def build_report(store: Store, cfg: Settings, recovery: RecoveryService, since_ms: int, until_ms: int,
                 program_id: str | None = None, now_ms: int | None = None) -> Report:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    pid = cfg.patient.id
    patient = store.get("patients", pid) or {"id": pid, "name": cfg.patient.name}
    program = store.get("programs", program_id) if program_id else None
    rep = Report("program" if program_id else "daily", patient, since_ms, until_ms, now_ms, program)

    rows = store.list("vitals_minute", "patient_id=? AND minute_ts>=? AND minute_ts<?", (pid, since_ms, until_ms),
                      order="minute_ts", limit=200_000)
    hrs = [r["hr_ecg"] if r["hr_ecg"] is not None else r["hr_ppg"] for r in rows]
    hr_ok = [h for h in hrs if h is not None]
    rest = [h for h, r in zip(hrs, rows, strict=True) if h is not None and r["activity"] in RESTING]
    sp = [r["spo2_avg"] for r in rows if r["spo2_avg"] is not None]
    sp_min = [r["spo2_min"] for r in rows if r["spo2_min"] is not None]
    temps = [r["temp"] for r in rows if r["temp"] is not None]
    rep.vitals = {
        "minutes_with_data": len(rows),
        "hr_min": round(min(hr_ok), 1) if hr_ok else None, "hr_avg": _mean(hr_ok),
        "hr_max": round(max(hr_ok), 1) if hr_ok else None, "hr_resting_avg": _mean(rest),
        "spo2_min": round(min(sp_min), 1) if sp_min else None, "spo2_avg": _mean(sp),
        "temp_min": round(min(temps), 1) if temps else None, "temp_avg": _mean(temps),
        "temp_max": round(max(temps), 1) if temps else None,
        "ews_max": max((r["ews"] or 0 for r in rows), default=0),
    }
    rep.activity_minutes = dict(Counter(r["activity"] or "unknown" for r in rows))
    flags: Counter[str] = Counter()
    for r in rows:
        for f in r["quality_flags"] or []:
            flags[f] += 1
    span_min = max(1, (until_ms - since_ms) // 60000)
    rep.data_quality = {"coverage_pct": round(min(100.0, 100.0 * len(rows) / span_min), 1),
                        "minutes_with_issues": dict(flags)}
    rep.hr_series = [(r["minute_ts"], h, r["hr_expected"]) for r, h in zip(rows, hrs, strict=True)]
    rep.spo2_series = [(r["minute_ts"], r["spo2_avg"]) for r in rows if r["spo2_avg"] is not None]

    by_hour: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_hour.setdefault(r["minute_ts"] // 3_600_000, []).append(r)
    for h in sorted(by_hour):
        g = by_hour[h]
        hh = [x["hr_ecg"] if x["hr_ecg"] is not None else x["hr_ppg"] for x in g]
        hh = [x for x in hh if x is not None]
        rep.hourly.append({"hour_ts": h * 3_600_000, "minutes": len(g), "hr_avg": _mean(hh),
                           "activity": Counter(x["activity"] for x in g).most_common(1)[0][0]})

    sleeping = [r for r in rows if r["activity"] == "sleeping"]
    events = store.list("events", "patient_id=? AND ts>=? AND ts<?", (pid, since_ms, until_ms), order="ts", limit=5000)
    rep.sleep = {"minutes": len(sleeping),
                 "hr_avg": _mean([r["hr_ecg"] for r in sleeping if r["hr_ecg"] is not None]),
                 "spo2_min": round(min((r["spo2_min"] for r in sleeping if r["spo2_min"] is not None), default=0), 1) or None,
                 "desat_alerts": sum(1 for e in events if e["kind"] == "sleep_desats")}
    rep.events = [{k: e[k] for k in ("id", "ts", "level", "kind", "message", "status", "sms_sent", "acknowledged_by")}
                  for e in events]
    rep.event_counts = dict(Counter(e["level"] for e in events))

    ranked = sorted((e for e in events if e["level"] != "info"), key=lambda e: (-LEVEL_RANK[e["level"]], -e["ts"]))
    for e in ranked:
        if len(rep.strips) >= MAX_STRIPS:
            break
        s = store.list("ecg_strips", "event_id=?", (e["id"],), limit=1)
        if not s:
            continue
        x = np.frombuffer(s[0]["samples"], dtype=np.float32)
        step = max(1, len(x) // STRIP_POINTS)
        rep.strips.append({"event_id": e["id"], "ts": e["ts"], "level": e["level"], "kind": e["kind"],
                           "message": e["message"], "fs": s[0]["fs"] / step, "samples": [float(v) for v in x[::step]]})

    rep.notes = [n for n in store.list("notes", "patient_id=? AND ts>=? AND ts<?", (pid, since_ms, until_ms),
                                       order="ts", limit=50)]
    if program and program["type"] == "recovery":
        prog = recovery.progress(program["id"])
        sessions = [s for s in store.list("exercise_sessions", "program_id=? AND start_ts>=? AND start_ts<?",
                                          (program["id"], since_ms, until_ms), order="start_ts", limit=2000)]
        rep.rehab = {"progress": prog, "sessions": sessions,
                     "plan": recovery.current_plan(program["id"]),
                     "assessments": store.list("assessments", "program_id=? AND ts>=? AND ts<?",
                                               (program["id"], since_ms, until_ms), order="ts", limit=2000)}
    return rep

"""Run the analysis on the CSV files a field study produces and write VALIDATION_REPORT.md.

CSV formats (header row required; templates in validation/templates/):
  paired (spo2 | hr | angle | temp):  subject,condition,t_s,device,reference
  battery:                            t_s,percent
  events:  the JSON from `GET /api/history?kind=events` (a file), plus --hours observed
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from .stats import Pair, battery_runtime, evaluate, false_alarm_rate


def read_paired(path: Path) -> list[Pair]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    need = {"subject", "condition", "t_s", "device", "reference"}
    if not rows or not need <= set(rows[0]):
        raise ValueError(f"{path.name}: needs columns {sorted(need)}")
    out = []
    for i, r in enumerate(rows, 2):
        try:
            out.append(Pair(float(r["t_s"]), float(r["device"]), float(r["reference"]), r["subject"].strip(), r["condition"].strip()))
        except ValueError as e:
            raise ValueError(f"{path.name} line {i}: {e}") from e
    return out


def read_battery(path: Path) -> list[tuple[float, float]]:
    with open(path, newline="") as f:
        return [(float(r["t_s"]), float(r["percent"])) for r in csv.DictReader(f)]


def render(results: dict[str, Any]) -> str:
    L = ["# SenseBand validation report", "",
         "> Measured by the people who ran the study. SenseBand is a monitoring aid, not a medical device; passing these "
         "criteria is not a medical clearance.", ""]
    for kind, v in results.get("agreement", {}).items():
        s = v.stats
        L += [f"## {kind.upper()}: {'PASS' if v.passed else 'FAIL'}", ""]
        if not s.get("ok"):
            L += [f"Not evaluated: {s.get('reason')}", ""]
            continue
        L += [f"- pairs: {s['n']}, subjects: {len(s['per_subject_mae'])}",
              f"- bias {s['bias']:+.2f}, 95% limits of agreement {s['loa_low']:+.2f} to {s['loa_high']:+.2f}",
              f"- MAE {s['mae']:.2f}, ARMS {s['arms']:.2f}, max error {s['max_abs_error']:.2f}, r = {s['r']:.3f}",
              f"- within +-{s['tol']}: {s['within_tol_pct']:.0f}%, reference range {s['ref_min']:.1f} to {s['ref_max']:.1f}",
              f"- per-subject MAE: {s['per_subject_mae']}"]
        L += [f"- **fails**: {r}" for r in v.reasons] + [f"- caveat: {c}" for c in v.caveats] + [""]
    fa = results.get("false_alarms")
    if fa:
        L += ["## False alarms (normal wear)", "", f"- observed {fa['hours']} h, {fa['total']} alerts",
              f"- critical {fa['critical']['per_24h']}/24h, warning {fa['warning']['per_24h']}/24h, info {fa['info']['per_24h']}/24h",
              f"- false fall alarms per 24 h: {fa['falls_per_24h']} (target < 1)", f"- SMS sent: {fa['sms_triggered']}, "
              f"cancelled by the person: {fa['cancelled_by_user']}", f"- by kind: {fa['by_kind']}", ""]
    b = results.get("battery")
    if b:
        L += ["## Battery", ""] + ([f"- {b['pct_per_hour']} %/h, projected {b['projected_hours']} h "
                                    f"(target {b['target_h']} h): {'meets' if b['meets_target'] else 'DOES NOT meet'} target",
                                    f"- fit R2 {b['r2']} over {b['observed_hours']} h. {b['note']}"] if b["ok"] else [f"- {b['reason']}"]) + [""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SenseBand field-study analysis")
    for k in ("spo2", "hr", "angle", "temp"):
        ap.add_argument(f"--{k}", type=Path, help=f"paired CSV for {k}")
    ap.add_argument("--battery", type=Path)
    ap.add_argument("--events", type=Path, help="JSON from /api/history?kind=events")
    ap.add_argument("--hours", type=float, help="hours of normal wear the events cover")
    ap.add_argument("--out", type=Path, default=Path("VALIDATION_REPORT.md"))
    a = ap.parse_args(argv)
    results: dict[str, Any] = {"agreement": {}}
    try:
        for k in ("spo2", "hr", "angle", "temp"):
            if getattr(a, k):
                results["agreement"][k] = evaluate(k, read_paired(getattr(a, k)))
        if a.battery:
            results["battery"] = battery_runtime(read_battery(a.battery))
        if a.events:
            if not a.hours:
                raise ValueError("--hours is required with --events")
            data = json.loads(a.events.read_text())
            results["false_alarms"] = false_alarm_rate(data["rows"] if isinstance(data, dict) else data, a.hours)
    except (ValueError, OSError, KeyError) as e:
        print("error:", e, file=sys.stderr)
        return 2
    a.out.write_text(render(results))
    print("wrote", a.out)
    return 0 if all(v.passed for v in results["agreement"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())

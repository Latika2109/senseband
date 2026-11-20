"""Milestone 7 tooling: the statistics must be right before they are trusted with a real study."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from senseband_edge.validation import study
from senseband_edge.validation.false_alarms_sim import ALLOWED, run_benign
from senseband_edge.validation.logger import COLUMNS, rows_from_message
from senseband_edge.validation.stats import (
    Pair,
    agreement,
    battery_runtime,
    evaluate,
    false_alarm_rate,
    pair_by_time,
    power_budget_hours,
)

from .harness import make_settings, run_scenario

TEMPLATES = Path(__file__).resolve().parents[2] / "validation" / "templates"


def synth(n_subj=8, n=40, bias=0.0, sd=1.0, lo=95.0, hi=99.0, seed=0, cond="rest"):
    rng = np.random.default_rng(seed)
    out = []
    for s in range(n_subj):
        for i in range(n):
            ref = rng.uniform(lo, hi)
            out.append(Pair(i * 60.0, ref + bias + rng.normal(0, sd), ref, f"V{s:02d}", cond))
    return out


def test_pair_by_time_nearest_within_tolerance_each_device_sample_once():
    dev = [(0, 10.0), (28, 11.0), (100, 12.0)]
    ref = [(1, 10.5), (30, 11.5), (31, 99.0), (500, 1.0)]
    p = pair_by_time(dev, ref, tol_s=5)
    assert [(x.device, x.reference) for x in p] == [(10.0, 10.5), (11.0, 11.5)]          # ref@31 has no unused sample in tol; ref@500 none
    assert pair_by_time([], ref) == [] and pair_by_time(dev, []) == []
    far = pair_by_time(dev, [(1000, 5.0)], tol_s=30)
    assert far == []
    q = pair_by_time(dev, [(0, 1.0)], subject="V9", condition="rest")[0]
    assert (q.subject, q.condition) == ("V9", "rest")


def test_agreement_recovers_known_bias_and_spread():
    st = agreement(synth(n_subj=20, n=250, bias=1.5, sd=2.0), tol=3.0)
    assert st["n"] == 5000 and abs(st["bias"] - 1.5) < 0.1 and abs(st["sd"] - 2.0) < 0.1
    assert abs(st["loa_high"] - (1.5 + 1.96 * 2.0)) < 0.25 and abs(st["loa_low"] - (1.5 - 1.96 * 2.0)) < 0.25
    assert abs(st["arms"] - math.sqrt(1.5 ** 2 + 2.0 ** 2)) < 0.1 and st["arms"] == st["rmse"]
    assert st["mae"] < st["arms"] and st["max_abs_error"] > st["arms"]
    assert 70 < st["within_tol_pct"] < 90 and -1 <= st["r"] <= 1
    assert agreement([Pair(0, 1, 1)] * 2, 1.0) == {"n": 2, "ok": False, "reason": "fewer than 3 pairs"}
    flat = agreement([Pair(i, 5.0, 5.0) for i in range(5)], 1.0)
    assert flat["bias"] == 0 and flat["within_tol_pct"] == 100 and math.isnan(flat["r"])


def test_spo2_verdicts_use_arms_and_warn_about_what_was_not_tested():
    good = evaluate("spo2", synth(bias=0.4, sd=1.0))
    assert good.passed and good.reasons == [] and any("94" in c for c in good.caveats)          # healthy range only
    bad = evaluate("spo2", synth(bias=5.0, sd=1.0))
    assert not bad.passed and any("ARMS" in r for r in bad.reasons)
    few = evaluate("spo2", synth(n_subj=3, bias=0.2, sd=1.0))
    assert few.passed and any("only 3 subjects" in c for c in few.caveats)
    low = evaluate("spo2", synth(lo=82, hi=99, bias=0.1, sd=1.0))
    assert not any("94" in c for c in low.caveats)
    pairs = synth(n_subj=6, bias=0.2, sd=0.8)
    pairs += [Pair(i, p.reference + 9, p.reference, "V99", "rest") for i, p in enumerate(pairs[:40])]
    assert any("much worse" in c for c in evaluate("spo2", pairs).caveats)
    assert not evaluate("spo2", [Pair(0, 1, 1)]).passed


def test_hr_and_angle_and_temp_criteria():
    hr = evaluate("hr", synth(bias=1.0, sd=2.0, lo=55, hi=150))
    assert hr.passed and not hr.caveats[1:] or hr.passed
    assert not evaluate("hr", synth(bias=7.0, sd=2.0, lo=55, hi=150)).passed
    assert any("range under 40" in c for c in evaluate("hr", synth(lo=60, hi=70)).caveats)
    rng = np.random.default_rng(1)
    ang = [Pair(i, a + rng.normal(0, 1.2), a, "V1", f"elbow {a}") for i, a in enumerate([0, 45, 90] * 12)]
    v = evaluate("angle", ang, min_subjects=1)
    assert v.passed and v.stats["max_abs_error"] < 5
    off = ang + [Pair(99, 52.0, 45.0, "V1", "elbow 45")]                                        # one 7-degree miss fails the acceptance
    assert not evaluate("angle", off, min_subjects=1).passed
    assert any("fewer than 3 reference angles" in c for c in evaluate("angle", ang[:6] and [Pair(i, 45, 45, "V1", "elbow 45") for i in range(5)], 1).caveats)
    assert evaluate("temp", synth(bias=0.1, sd=0.2, lo=36, hi=38)).passed and not evaluate("temp", synth(bias=1.0, sd=0.1, lo=36, hi=38)).passed


def test_false_alarm_rate_per_day_and_by_kind():
    ev = [{"kind": "fall", "level": "critical", "status": "cancelled"}, {"kind": "fall", "level": "critical", "status": "sms_sent"},
          {"kind": "hr_high_rest", "level": "warning", "status": "notified"}, {"kind": "check_sensor_ppg", "level": "info", "status": "notified"}]
    fa = false_alarm_rate(ev, hours_observed=12)
    assert fa["total"] == 4 and fa["critical"] == {"count": 2, "per_24h": 4.0} and fa["falls_per_24h"] == 4.0
    assert fa["sms_triggered"] == 1 and fa["cancelled_by_user"] == 1 and fa["by_kind"]["fall"] == 2
    assert false_alarm_rate(ev, 24, exclude_kinds=("fall",))["total"] == 2
    with pytest.raises(ValueError):
        false_alarm_rate(ev, 0)


def test_battery_projection_and_power_budget():
    lin = [(t * 600.0, 100 - 4.0 * t / 6) for t in range(20)]                                   # 4 %/h
    b = battery_runtime(lin)
    assert b["ok"] and abs(b["projected_hours"] - 25.0) < 0.2 and b["meets_target"] and b["r2"] > 0.999
    slow = battery_runtime([(t * 600.0, 100 - 6.0 * t / 6) for t in range(20)])
    assert slow["projected_hours"] < 24 and not slow["meets_target"]
    for bad, why in (([(0, 100)] * 3, "5 readings"), ([(t * 10.0, 100 - t) for t in range(10)], "30 minutes"),
                     ([(t * 600.0, 90) for t in range(10)], "not discharging")):
        r = battery_runtime(bad)
        assert not r["ok"] and why in r["reason"]
    assert abs(power_budget_hours({"pi+sensors": 440.0}, 7500.0) - 17.05) < 0.1                  # docs/03: 17-20 h on 3 cells
    assert power_budget_hours({"pi zero 2 w": 200.0}, 5000.0) == 25.0


def test_logger_rows_keep_missing_values_empty_and_ignore_other_messages():
    v = {"t": "vitals", "hr": None, "hr_src": None, "spo2": 96.0, "temp": 36.4, "context": "sitting", "quality": {"ecg": "lead_off", "ppg": "good"}}
    assert rows_from_message(v, 1.234) == [[1.23, None, None, 96.0, 36.4, "sitting", "lead_off", "good", "", ""]]
    assert rows_from_message({"t": "angle", "angle": 44.5, "movement": "knee_flexion"}, 2.0)[0][-2:] == [44.5, "knee_flexion"]
    assert rows_from_message({"t": "ecg", "samples": [1]}, 0) == [] and rows_from_message({}, 0) == []
    assert len(COLUMNS) == len(rows_from_message(v, 0)[0])


def test_cli_on_the_shipped_templates_and_on_bad_input(tmp_path, capsys):
    out = tmp_path / "r.md"
    code = study.main(["--spo2", str(TEMPLATES / "spo2.csv"), "--hr", str(TEMPLATES / "hr.csv"), "--angle", str(TEMPLATES / "angle.csv"),
                       "--battery", str(TEMPLATES / "battery.csv"), "--out", str(out)])
    text = out.read_text()
    assert code in (0, 1) and "# SenseBand validation report" in text and "## SPO2" in text and "## ANGLE" in text and "## Battery" in text
    assert "not a medical device" in text and "only 1 subjects" in text                           # the template is one volunteer: flagged
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    assert study.main(["--hr", str(bad), "--out", str(out)]) == 2 and "needs columns" in capsys.readouterr().err
    nonnum = tmp_path / "nn.csv"
    nonnum.write_text("subject,condition,t_s,device,reference\nV1,rest,0,abc,70\n")
    assert study.main(["--hr", str(nonnum), "--out", str(out)]) == 2
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"rows": [{"kind": "fall", "level": "critical", "status": "cancelled"}]}))
    assert study.main(["--events", str(ev), "--out", str(out)]) == 2                                # --hours required
    assert study.main(["--events", str(ev), "--hours", "48", "--out", str(out)]) == 0
    assert "False alarms" in out.read_text() and "false fall alarms per 24 h: 0.5" in out.read_text()


def test_a_simulated_study_flows_through_the_whole_toolchain():
    """Band readings from the real engine vs the simulator's ground truth, analysed like a field study (NOT an accuracy claim)."""
    pairs = []
    for subj, seed in enumerate(range(5)):
        r = run_scenario("resting", 150, seed=seed)
        dev = [((v["ts"] - r.engine.base_ms) / 1000.0, v["hr"]) for v in r.vitals if v["hr"] is not None and v["quality"]["ecg"] == "good"]
        ref = [(float(t), 72.0) for t in range(30, 150, 5)]
        pairs += pair_by_time(dev, ref, tol_s=1.0, subject=f"sim{subj}", condition="rest")
    v = evaluate("hr", pairs)
    assert v.stats["n"] > 100 and v.passed and v.stats["mae"] < 3
    assert any("range under 40" in c for c in v.caveats)                                           # the report flags the narrow range


@pytest.mark.slow
def test_no_false_critical_alarms_on_benign_simulated_scenarios_across_seeds():
    res = run_benign(["resting", "elbow_exercise", "knee_exercise", "grip_test", "lead_off", "noisy_ecg", "sleeping"], [1, 2, 3, 4], 200)
    assert res["false_alarms"] == [] and res["false_critical"] == 0 and res["hours_simulated"] > 0.5
    assert set(ALLOWED) >= {"resting", "jogging", "sleeping"}
    jog = run_benign(["jogging"], [5, 6], 330)
    assert jog["false_alarms"] == []
    models = run_benign(["resting", "jogging"], [7], 330, settings=make_settings(models={"enabled": True}))
    assert models["false_alarms"] == []                                                              # also with the ML models switched on

"""Milestone 5 (edge side): ONNX models replace rule-based fallbacks safely."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from senseband_edge.alerts.sms import MockSms
from senseband_edge.api.app import create_app
from senseband_edge.context.engine import Engine
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.source import SimSource
from senseband_edge.models.registry import ModelRegistry
from senseband_edge.service import EdgeService
from senseband_edge.storage.db import Store
from senseband_edge.storage.raw import RawStore

from .harness import kinds, make_settings, run_scenario

REAL = Path(__file__).resolve().parents[2] / "ml" / "models"
needs_models = pytest.mark.skipif(not (REAL / "metrics.json").exists(), reason="models not trained (make train)")
ON = make_settings(models={"enabled": True})


def reg(**over) -> ModelRegistry:  # noqa: ANN003
    return ModelRegistry(make_settings(models={"enabled": True, **over}).models)


# ---- registry: every way a model can be unusable falls back, never crashes ------------------------------
def test_disabled_missing_and_unreadable_models_fall_back_with_a_reason(tmp_path):
    r = ModelRegistry(make_settings(models={"enabled": False}).models)
    assert r.get("activity") is None and r.reasons["*"] == "disabled in config"
    r = reg(dir=str(tmp_path))
    assert r.models == {} and "metrics.json" in r.reasons["*"]
    (tmp_path / "metrics.json").write_text("{}")
    r = reg(dir=str(tmp_path))
    assert all("missing" in r.reasons[k] for k in ("activity", "ecg_quality", "rhythm", "expected_hr"))
    assert r.status()["activity"] == {"active": False, "reason": r.reasons["activity"]}


@needs_models
def test_real_models_load_and_report_status():
    r = reg()
    st = r.status()
    assert [k for k, v in st.items() if v["active"]] == ["activity", "ecg_quality", "rhythm", "expected_hr"]
    assert st["fall"]["reason"] == "disabled in config"                        # simulated-only model stays off by default


@needs_models
def test_feature_version_mismatch_oversize_and_bad_files_are_rejected(tmp_path):
    for f in REAL.iterdir():
        if f.suffix in (".onnx", ".json"):
            shutil.copy(f, tmp_path / f.name)
    m = json.loads((tmp_path / "metrics.json").read_text())
    m["m1_activity"]["feature_version"] = 99
    m["m3_ecg_quality"]["onnx"]["bytes"] = 6 * 1024 * 1024
    (tmp_path / "m4_rhythm.onnx").write_bytes(b"not an onnx file")
    (tmp_path / "metrics.json").write_text(json.dumps(m))
    r = reg(dir=str(tmp_path))
    assert "retrain" in r.reasons["activity"] and "5 MB" in r.reasons["ecg_quality"] and "load failed" in r.reasons["rhythm"]
    assert r.get("expected_hr") is not None                                     # the healthy one still works


@needs_models
def test_slow_failing_or_mismatched_models_switch_themselves_off():
    r = reg(max_infer_ms=1e-6)                                                  # impossible budget
    m = r.models["activity"]
    x = np.zeros(len(m.meta["features"]), np.float32)
    for _ in range(25):
        m.run(x)
    assert m.ok is False and r.get("activity") is None and m.run(x) is None
    r2 = reg()
    assert r2.models["rhythm"].run(np.zeros(3, np.float32)) is None             # wrong number of features
    assert r2.get("rhythm") is None
    r3 = reg()
    r3.models["ecg_quality"].sess = None                                        # session blows up
    assert r3.models["ecg_quality"].run(np.zeros(len(r3.models["ecg_quality"].meta["features"]), np.float32)) is None
    assert r3.get("ecg_quality") is None


# ---- engine behaviour with the models on (they must not break the safe behaviour) ------------------------
@needs_models
def test_models_do_not_change_the_safe_behaviour_on_simulator_scenarios():
    r = run_scenario("resting", settings=ON)
    assert r.engine.alerts.alerts == [] and r.vitals[-1]["context"] == "sitting" and r.vitals[-1]["quality"]["ecg"] == "good"
    r = run_scenario("resting_tachycardia", settings=ON)
    assert kinds(r) == ["hr_high_rest"] and r.sms.log == []
    r = run_scenario("fall", settings=ON)
    assert kinds(r) == ["fall"] and len(r.sms.delivered) == 1
    r = run_scenario("collapse", settings=ON)
    assert kinds(r) == ["collapse"] and len(r.sms.delivered) == 1
    r = run_scenario("lead_off", settings=ON)
    assert all(k.startswith("check_sensor") for k in kinds(r)) and r.sms.log == []
    r = run_scenario("noisy_ecg", settings=ON)
    assert set(kinds(r)) == {"check_sensor_ecg"}
    r = run_scenario("sleeping", settings=ON)
    assert kinds(r, "critical") == [] and "sleeping" in {v["context"] for v in r.vitals}


@needs_models
@pytest.mark.slow
def test_jogging_with_models_has_no_alerts_and_running_is_recognised():
    r = run_scenario("jogging", settings=ON)
    assert r.engine.alerts.alerts == [] and r.sms.log == []
    ctx = [v["context"] for v in r.vitals]
    assert ctx.count("running") > 300 and "walking" in ctx
    assert r.engine.act.result.source in ("model", "rules")


@needs_models
def test_activity_model_is_used_for_static_postures_and_rules_keep_running_and_sleep():
    r = run_scenario("sleeping", 200, settings=ON)
    e = r.engine
    assert e.act.result.source == "model" and e.act.result.prob and e.act.result.prob >= 0.55
    assert {v["context"] for v in r.vitals} >= {"lying", "sleeping"}                     # sleeping rule on top of the model
    off = run_scenario("sleeping", 200, settings=make_settings(models={"enabled": False}))
    assert off.engine.act.result.source == "rules"
    j = run_scenario("jogging", 200, settings=ON)
    assert any(v["context"] == "running" for v in j.vitals)                              # raw-energy rule beats the model


@needs_models
def test_rhythm_model_flags_af_and_not_normal_rhythm():
    af = run_scenario("atrial_fibrillation", settings=ON)
    assert "irregular_rhythm" in kinds(af)
    a = next(a for a in af.engine.alerts.alerts if a.kind == "irregular_rhythm")
    assert a.t > 60 and "not a diagnosis" in a.message
    assert any(v["rhythm"] == "possible_irregular" for v in af.vitals)
    ok = run_scenario("resting", settings=ON)
    assert {v["rhythm"] for v in ok.vitals} <= {"regular", "unknown"}


@needs_models
def test_expected_hr_model_gives_a_context_range_and_learns_the_person(tmp_path):
    r = run_scenario("resting", 240, settings=ON)
    v = r.vitals[-1]
    assert 55 < v["hr_expected"] < 95 and v["hr_range"][0] < v["hr_expected"] < v["hr_range"][1]
    assert r.engine.personal.n > 0
    store = Store(str(tmp_path / "p.db"))
    e = Engine(ON, store, MockSms(), SimIO(), base_ms=0)
    e.personal.update(90.0, 99.0)
    e._personal_saved = -10_000  # noqa: SLF001
    e._expected_hr(700.0, e.act.result, 70.0, "ecg", False, None, False)    # triggers the periodic save
    e2 = Engine(ON, store, MockSms(), SimIO(), base_ms=0)
    assert e2.personal.n >= 1 and abs(e2.personal.correct(90.0) - e.personal.correct(90.0)) < 1e-6


@needs_models
def test_learning_period_widens_the_expected_band_and_is_flagged():
    cfg = ON
    svc = EdgeService(cfg, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(":memory:"))
    assert svc.engine.learning_start_ms is not None
    with TestClient(create_app(svc)) as c:
        t0 = time.time()
        while time.time() - t0 < 30 and not svc.engine.vitals.get("hr_range"):
            time.sleep(0.05)
        v = svc.engine.vitals
        assert v["learning"] is True
        st = c.get("/api/status").json()
        assert st["learning_h"] == 48 and st["models"]["activity"]["active"] is True
        assert c.get("/api/models").json()["models"]["fall"]["active"] is False
    band = v["hr_range"][1] - v["hr_range"][0]
    done = run_scenario("resting", 90, settings=ON)                               # no learning start => normal band
    assert band > done.vitals[-1]["hr_range"][1] - done.vitals[-1]["hr_range"][0]


# ---- fall model veto (off by default) ------------------------------------------------------------------
@needs_models
def test_fall_model_can_veto_a_trigger_but_a_real_sim_fall_still_alerts():
    on = make_settings(models={"enabled": True, "fall": {"enabled": True, "veto_below": 0.3}})
    r = run_scenario("fall", settings=on)
    assert kinds(r) == ["fall"] and r.engine.stats["fall_vetoed"] == 0
    always = make_settings(models={"enabled": True, "fall": {"enabled": True, "veto_below": 1.01}})
    v = run_scenario("fall", settings=always)
    assert kinds(v) == [] and v.engine.stats["fall_vetoed"] == 1 and v.sms.log == []     # veto cancels the SMS path
    default = run_scenario("fall", settings=ON)
    assert kinds(default) == ["fall"] and default.engine.stats["fall_vetoed"] == 0       # fall model disabled by default


# ---- record training data -------------------------------------------------------------------------------
def svc_rec(tmp_path):
    cfg = make_settings(storage={"recordings_dir": str(tmp_path / "rec"), "max_recording_s": 8})
    return EdgeService(cfg, SimSource("jogging", speed=30.0, loop=True), MockSms(), SimIO(), Store(":memory:"))


def wait(cond, t=30.0):
    t0 = time.time()
    while time.time() - t0 < t and not cond():
        time.sleep(0.05)
    return cond()


def test_record_mode_start_stop_validation_and_cap(tmp_path):
    svc = svc_rec(tmp_path)
    with TestClient(create_app(svc)) as c:
        assert wait(lambda: svc.engine.now > 2)
        assert c.post("/api/record/stop").status_code == 409
        for bad in ("", "Walking", "has space", "x" * 41, "../../etc"):
            assert c.post("/api/record/start", json={"label": bad}).status_code == 422, bad
        assert c.post("/api/record/start", json={"label": "walking-fast"}).json() == {"recording": True, "label": "walking-fast"}
        assert c.post("/api/record/start", json={"label": "other"}).status_code == 409
        assert c.get("/api/record").json()["recording"] is True
        assert wait(lambda: svc.engine.stats["recording_capped"] > 0)                    # RAM cap reached
        info = c.post("/api/record/stop").json()
        assert info["label"] == "walking-fast" and 7.5 <= info["seconds"] <= 10 and info["bytes"] > 1000
        z = np.load(info["path"])
        assert z["acc_g"].shape[1] == 3 and len(z["ecg_mv"]) > 1500 and str(z["meta_label"]) == "walking-fast"
        assert c.get("/api/record").json()["files"][0]["file"].endswith("_walking-fast.npz")
        assert c.post("/api/record/stop").status_code == 409


# ---- rolling raw store ----------------------------------------------------------------------------------
def test_raw_store_writes_minute_chunks_and_prunes_old_ones():
    from senseband_edge.hw.sim import Simulator
    s = Store(":memory:")
    raw = RawStore(s, days=7)
    for k, f in enumerate(Simulator("resting").frames(130)):
        raw.add(f, 1_700_000_000_000 + k * 200)
    raw.flush()
    rows = s.query("SELECT ts, stream, length(payload) AS n FROM raw_chunks ORDER BY ts, stream")
    assert len(rows) >= 4 and {r["stream"] for r in rows} == {"ecg_mv_x1000_i16", "acc_mg_i16"}
    assert all(r["n"] > 100 and r["ts"] % 60000 == 0 for r in rows)
    removed = raw.prune(now_ms=1_700_000_000_000 + 8 * 86_400_000)
    assert removed == len(rows) and s.query("SELECT COUNT(*) AS n FROM raw_chunks")[0]["n"] == 0
    assert raw.prune(now_ms=0) == 0
    e = Engine(make_settings(storage={"keep_raw": True}), s, MockSms(), SimIO(), base_ms=1_700_000_000_000)
    assert e.raw is not None and Engine(make_settings(), s, MockSms(), SimIO()).raw is None   # off by default

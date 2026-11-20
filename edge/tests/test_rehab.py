"""Milestone 3: rehab engine - angles, reps, plans, forecast, assessments, exercise sessions."""
from __future__ import annotations

import math
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.spatial.transform import Rotation as R
from senseband_edge.alerts.sms import MockSms
from senseband_edge.api.app import create_app
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.source import SimSource
from senseband_edge.rehab import library
from senseband_edge.rehab.engine import RehabError
from senseband_edge.rehab.forecast import forecast
from senseband_edge.rehab.joints import JOINTS, MOVEMENTS, JointAngle, movements_of
from senseband_edge.rehab.plan import generate_plan, rom_percent, stage_for
from senseband_edge.rehab.reps import RepCounter
from senseband_edge.service import EdgeService
from senseband_edge.storage.db import Store

from .harness import make_settings, run_scenario

CFG = make_settings()
RC = CFG.rehab


def wxyz(r: R) -> tuple[float, float, float, float]:
    x, y, z, w = r.as_quat()
    return (float(w), float(x), float(y), float(z))


# ---- joint angle from relative quaternions ------------------------------------------------------
@pytest.mark.parametrize("movement", sorted(MOVEMENTS))
@pytest.mark.parametrize("deg", [0, 45, 90])
def test_angle_error_under_1_degree_for_every_movement_at_0_45_90(movement, deg):
    """Acceptance (simulated goniometer): <= 5 deg error at 0/45/90 deg; we require < 1 deg."""
    m = MOVEMENTS[movement]
    rng = np.random.default_rng(7)
    ref0 = R.random(random_state=3)                              # arbitrary mounting / heading
    mount = R.from_euler("xyz", [8, -6, 20], degrees=True)       # imperfect strap alignment
    axis = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[m.axis]
    mov = lambda a, ref: ref * mount * R.from_rotvec(np.deg2rad(m.sign * a) * np.array(axis))  # noqa: E731
    pairs = [(wxyz(ref0), wxyz(mov(0, ref0)))] * 30
    neutral, wob = JointAngle.calibrate(pairs, 3.0)
    ja = JointAngle(neutral)
    assert wob < 0.01
    ref = ref0 * R.from_euler("z", 77, degrees=True)             # yaw drift of the reference band
    # moving band yaws by the same amount => relative rotation unchanged
    q_mov = ref * mount * R.from_rotvec(np.deg2rad(m.sign * deg) * np.array(axis))
    ang, off = ja.angle(wxyz(ref0), wxyz(mov(deg, ref0)), movement)
    assert abs(ang - deg) < 1.0 and off < 1.0
    ang2, _ = ja.angle(wxyz(ref), wxyz(q_mov), movement)
    assert abs(ang2 - deg) < 1.0                                  # common yaw cancels
    noisy = R.from_rotvec(rng.normal(0, np.deg2rad(0.2), 3))      # sensor noise
    ang3, _ = ja.angle(wxyz(ref0), wxyz(mov(deg, ref0) * noisy), movement)
    assert abs(ang3 - deg) < 1.5


def test_calibration_rejects_a_moving_limb():
    ref = R.identity()
    pairs = [(wxyz(ref), wxyz(R.from_euler("y", a, degrees=True))) for a in np.linspace(0, 20, 30)]
    with pytest.raises(ValueError, match="not steady"):
        JointAngle.calibrate(pairs, 3.0)


def test_six_joints_have_movements_normal_rom_and_every_stage():
    assert set(JOINTS) == {"shoulder", "elbow", "wrist", "hip", "knee", "ankle"}
    for j in JOINTS:
        assert movements_of(j), j
        for st in (1, 2, 3):
            assert library.for_joint(j, st), (j, st)
    assert set(MOVEMENTS) == set(RC.normal_rom)
    for ex in library.library().values():
        assert MOVEMENTS[ex["movement"]].joint == ex["joint"]
        assert 0 < ex["target_pct"] <= 100 and len(ex["tempo_s"]) == 2
        assert ex["name"] and ex["name_hi"] and ex["how"]
    with pytest.raises(KeyError):
        library.get("nope")


# ---- repetition counter -------------------------------------------------------------------------
def feed(rc: RepCounter, fn, secs: float, hz: int = 50, t0: float = 0.0):
    for i in range(int(secs * hz)):
        rc.update(fn(i / hz), t0 + i / hz)


def test_rep_counter_counts_reps_ignores_jitter_and_measures_tempo():
    rng = np.random.default_rng(1)
    tri = lambda t: max(0.0, 90 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 6)) + rng.normal(0, 0.8))  # noqa: E731
    rc = RepCounter(90, min_rep_s=4.0)
    feed(rc, tri, 6 * 8)                                          # 8 reps of 6 s
    assert rc.count == 8
    # measured between the 20 % crossings a 6 s smooth cycle lasts 6 * (1 - acos(0.6)/pi) = 4.23 s
    assert all(abs(r.peak_deg - 90) < 4 and abs(r.duration_s - 4.23) < 0.4 for r in rc.reps)
    assert not any(r.too_fast for r in rc.reps)
    s = rc.summary()
    assert s["target_hit_pct"] == 100 and abs(s["avg_peak_angle"] - 90) < 4 and s["too_fast_count"] == 0


def test_rep_counter_flags_too_fast_and_ignores_small_wiggles_and_resting_noise():
    fast = lambda t: 90 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 2))  # noqa: E731
    rc = RepCounter(90, min_rep_s=4.2)
    feed(rc, fast, 20)
    assert rc.count == 10 and rc.summary()["too_fast_count"] == 10
    rc = RepCounter(90)
    feed(rc, lambda t: 25 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 3)), 30)   # peaks only 25 deg < 50 % target
    assert rc.count == 0
    rc = RepCounter(90)
    feed(rc, lambda t: float(np.random.default_rng(int(t * 50)).normal(2, 1)), 20)
    assert rc.count == 0


def test_nominal_duration_matches_what_the_counter_measures_for_a_perfectly_paced_rep():
    from senseband_edge.rehab.reps import nominal_duration
    assert nominal_duration(3, 3, 0.2) == pytest.approx(4.2288, abs=1e-3)
    for up, down in ((3, 3), (4, 4), (2, 5)):
        rc = RepCounter(90, down_frac=0.2)
        T = up + down
        tri = lambda t, up=up, T=T: 90 * (0.5 - 0.5 * math.cos(math.pi * (t % T) / up)) if (t % T) < up else 90 * (0.5 + 0.5 * math.cos(math.pi * ((t % T) - up) / (T - up)))  # noqa: E731
        feed(rc, tri, T * 5)
        assert rc.count == 5 and all(abs(r.duration_s - nominal_duration(up, T - up, 0.2)) < 0.15 for r in rc.reps), (up, down)


def test_rep_counter_over_limit_and_hold_time():
    rc = RepCounter(90, limit_deg=100)
    def prof(t):
        u = t % 10
        return 105.0 if 3 <= u < 7 else max(0.0, 105 * min(u / 3, 1) if u < 3 else 105 * (10 - u) / 3)
    feed(rc, prof, 20)
    assert rc.count == 2 and all(r.over_limit for r in rc.reps)
    assert all(3.0 < r.hold_s < 5.5 for r in rc.reps)            # time spent in the target zone


# ---- plan generator -----------------------------------------------------------------------------
def test_stage_from_rom_percent():
    assert rom_percent(RC, "elbow_flexion", 60) == pytest.approx(40)
    assert [stage_for(RC, p) for p in (10, 39.9, 40, 74.9, 75, 100)] == [1, 1, 2, 2, 3, 3]


def plan(rom, pain=None, days=60, adh=None, limits=None, doctor=False):
    return generate_plan(RC, "elbow", "elbow_flexion", rom, pain, days, adh, limits, doctor)


def test_plan_stages_pain_early_injury_and_adherence_rules():
    assert plan(40)["stage"] == 1 and plan(90)["stage"] == 2 and plan(130)["stage"] == 3
    assert plan(130, pain=5)["stage"] == 2 and "pain_kept_gentle" in plan(130, pain=5)["notes"]
    assert plan(130, pain=8)["stage"] == 1 and "pain_high_rest" in plan(130, pain=8)["notes"]
    assert plan(130, days=7)["stage"] == 1 and "early_injury_gentle" in plan(130, days=7)["notes"]
    base = plan(90)["items"][0]["reps"]
    low = plan(90, adh=0.2)
    assert low["items"][0]["reps"] == max(5, base - 3) and "low_adherence_no_increase" in low["notes"]


def test_plan_never_exceeds_doctor_limit_and_needs_approval():
    p = plan(130, limits={"elbow_flexion": 100})
    assert p["stage"] == 3 and all(i["target_angle"] <= 100 for i in p["items"] if i["movement"] == "elbow_flexion")
    assert p["items"][0]["limit_angle"] == 100
    assert not p["may_start"] and "needs_doctor_approval" in p["notes"]
    g = plan(40)
    assert g["may_start"] and "stage1_may_start_without_doctor" in g["notes"]
    assert plan(130, doctor=True)["may_start"]
    assert all(i["exercise_id"] in library.library() for i in p["items"])


# ---- forecast -----------------------------------------------------------------------------------
def test_forecast_recovers_synthetic_curve_with_a_sensible_interval():
    rng = np.random.default_rng(4)
    days = np.arange(0, 28, 2.0)
    rom = 130 - (130 - 40) * np.exp(-0.08 * days) + rng.normal(0, 1.5, len(days))
    f = forecast(list(days), list(rom), target=117, normal=135, n_boot=150)
    assert f["ok"] and f["note"] == "estimate only"
    true_days = -math.log((130 - 117) / 90) / 0.08               # ~ 24 days
    assert abs(f["est_days"] - true_days) < 6
    assert f["ci_low_days"] <= f["est_days"] <= f["ci_high_days"]
    assert abs(f["params"]["r_inf"] - 130) < 10


def test_forecast_needs_enough_sessions_and_handles_plateau():
    f = forecast([0, 1, 2, 3], [40, 50, 60, 70], 100, 135)
    assert not f["ok"] and f["reason"] == "not_enough_sessions"
    flat = forecast(list(range(0, 12, 2)), [80, 90, 95, 96, 96, 96], target=130, normal=135)
    assert flat["ok"] and flat["est_days"] is None and flat["reason"] == "target_not_reachable_on_trend"
    done = forecast(list(range(0, 12, 2)), [100, 110, 118, 122, 124, 125], target=90, normal=135)
    assert done["ok"] and done["est_days"] == 0.0


# ---- engine on simulator scenarios --------------------------------------------------------------
def drive(name, actions, seconds=None):
    """actions: list of (time_s, fn(eng)). Runs the scenario and fires them in data time."""
    pending = sorted(actions, key=lambda a: a[0])
    out: dict = {}

    def on_frame(eng, f):
        while pending and eng.now >= pending[0][0]:
            t, fn = pending.pop(0)
            out[t] = fn(eng)
    r = run_scenario(name, seconds, on_frame=on_frame)
    return r, out


def test_elbow_calibration_rom_and_exercise_end_to_end():
    def start_ex(e):
        return e.rehab.start_exercise("elbow_active_flexion", {"sets": 1, "reps": 10, "target_angle": 100})
    r, out = drive("elbow_exercise", [
        (3.0, lambda e: e.rehab.start_calibration("elbow")),
        (9.0, lambda e: e.rehab.state()),
        (38.0, start_ex),
    ])
    eng = r.engine
    assert out[9.0]["calibrated"] == ["elbow"] and out[9.0]["result"]["ok"]
    res = eng.rehab.last_result
    assert res["kind"] == "session" and res["stopped_reason"] == "completed"
    assert res["reps"] == 10                                      # +-1 allowed, we expect exact
    assert abs(res["avg_peak_angle"] - 110) < 5 and abs(res["max_angle"] - 110) < 6
    assert res["target_hit_pct"] == 100.0
    assert res["too_fast_count"] == 0                            # the sim moves at the planned pace: never flagged
    cmds = eng.band_emulator.commands
    assert cmds[0] == "RATE,25" and "RATE,50" in cmds and cmds[-1] == "RATE,10"
    row = r.store.list("exercise_sessions")[0]
    assert row["reps_done"] == 10 and row["stopped_reason"] == "completed" and row["exercise_id"] == "elbow_active_flexion"
    assert len(r.store.list("calibrations")) == 1
    angles = [m for m in r.msgs if m["t"] == "angle"]
    reps = [m for m in r.msgs if m["t"] == "rep"]
    assert len(angles) > 400 and len(reps) == 10 and reps[-1]["set"] == 1 and reps[-1]["rep_in_set"] == 10
    assert max(a["angle"] for a in angles) > 100 and {"in_target", "too_fast", "over_limit", "rep"} <= set(angles[0])
    assert any(m["t"] == "exercise_done" for m in r.msgs)
    assert r.engine.alerts.alerts == []                           # exercise raises no vital alerts


def test_rom_assessment_matches_truth_and_is_saved():
    r, out = drive("elbow_exercise", [
        (3.0, lambda e: e.rehab.start_calibration("elbow")),
        (38.0, lambda e: e.rehab.start_rom("elbow_flexion", "pre", "right")),
        (100.0, lambda e: e.rehab.stop_rom()),
    ])
    res = out[100.0]
    assert abs(res["max_angle"] - 110) < 5 and abs(res["min_angle"]) < 3 and abs(res["rom"] - 110) < 6
    assert res["phase"] == "pre" and res["side"] == "right" and not res["over_limit"]
    row = r.store.list("assessments")[0]
    assert row["kind"] == "rom" and abs(row["rom"] - res["rom"]) < 1e-6 and row["movement"] == "elbow_flexion"


def test_knee_exercise_uses_the_knee_sign_convention():
    r, _ = drive("knee_exercise", [
        (3.0, lambda e: e.rehab.start_calibration("knee")),
        (38.0, lambda e: e.rehab.start_exercise("knee_active_flexion", {"sets": 1, "reps": 8, "target_angle": 90})),
    ])
    res = r.engine.rehab.last_result
    assert res["reps"] == 8 and abs(res["avg_peak_angle"] - 95) < 5


def test_doctor_limit_is_enforced_with_haptics_and_flag():
    from senseband_edge.rehab.engine import RehabEngine  # noqa: F401
    def setup(e):
        e.rehab.start_calibration("elbow")
    def go(e):
        e.program_id = "pr-limit"
        e.rehab.store.upsert("recovery_cases", {"program_id": e.program_id, "joint": "elbow", "side": "right",
                                                "max_angle_limits_json": {"elbow_flexion": 90}})
        return e.rehab.start_exercise("elbow_active_flexion", {"sets": 1, "reps": 4})
    r, _ = drive("elbow_exercise", [(3.0, setup), (38.0, go)], seconds=80)
    assert any(m["t"] == "limit_exceeded" and m["limit"] == 90 for m in r.msgs)
    assert any(c.startswith("VIB,") for c in r.engine.band_emulator.commands)
    assert any(m.get("over_limit") for m in r.msgs if m["t"] == "angle")


def test_pain_7_stops_the_session_and_high_pain_blocks_starting():
    def pain(e):
        return e.rehab.record_pain(8, "during")
    r, out = drive("elbow_exercise", [
        (3.0, lambda e: e.rehab.start_calibration("elbow")),
        (38.0, lambda e: e.rehab.start_exercise("elbow_active_flexion", {"sets": 1, "reps": 10}, pain_before=3)),
        (60.0, pain),
    ], seconds=70)
    assert out[60.0]["stopped_exercise"] is True
    res = r.engine.rehab.last_result
    assert res["stopped_reason"] == "pain" and res["pain_after"] == 8 and 2 <= res["reps"] < 10
    assert r.store.list("exercise_sessions")[0]["stopped_reason"] == "pain"
    with pytest.raises(RehabError) as e:
        r.engine.rehab.start_exercise("elbow_active_flexion", pain_before=7)
    assert e.value.code == "pain_too_high"


def test_guards_not_calibrated_busy_not_running_unknowns():
    r = run_scenario("elbow_exercise", 5)
    rh = r.engine.rehab
    for fn, code in ((lambda: rh.start_rom("elbow_flexion"), "not_calibrated"),
                     (lambda: rh.start_exercise("elbow_active_flexion"), "not_calibrated"),
                     (lambda: rh.stop_rom(), "not_running"), (lambda: rh.stop_exercise(), "not_running"),
                     (lambda: rh.start_calibration("tail"), "unknown_joint"),
                     (lambda: rh.record_pain(11, "x"), "bad_score")):
        with pytest.raises(RehabError) as e:
            fn()
        assert e.value.code == code
    rh.start_calibration("elbow")
    with pytest.raises(RehabError) as e:
        rh.start_calibration("knee")
    assert e.value.code == "busy"
    with pytest.raises(KeyError):
        rh.start_exercise("nope")


def test_calibration_fails_cleanly_while_the_limb_moves():
    r, out = drive("elbow_exercise", [(44.0, lambda e: e.rehab.start_calibration("elbow")),
                                       (52.0, lambda e: e.rehab.state())], seconds=55)
    res = out[52.0]["result"]
    assert res["ok"] is False and res["error"] == "not_steady" and out[52.0]["calibrated"] == []


def test_grip_test_peak_and_mean_in_kg():
    r, _ = drive("resting", [(2.0, lambda e: e.rehab.start_grip("right"))], seconds=15)
    g = r.engine.rehab.last_result
    assert g["ok"] and g["unit"] == "kg" and 18 < g["grip_peak"] < 25 and 10 < g["grip_mean"] <= g["grip_peak"]
    row = r.store.list("assessments")[0]
    assert row["kind"] == "grip" and row["side"] == "right" and row["grip_peak"] == g["grip_peak"]


def test_grip_with_no_squeeze_or_missing_sensor_ends_with_a_clear_failure():
    def nosqueeze(e):
        e.rehab.grip_hook = lambda on: None                # sensor never produces a squeeze
        return e.rehab.start_grip("left")
    r, _ = drive("resting", [(2.0, nosqueeze)], seconds=20)
    assert r.engine.rehab.mode == "idle"
    assert r.engine.rehab.last_result == {"kind": "grip", "side": "left", "ok": False, "error": "no_squeeze"}


def test_calibration_times_out_when_the_small_band_is_not_connected():
    def drop(e):
        e.band_emulator.connected = False
        return e.rehab.start_calibration("elbow")
    r, out = drive("elbow_exercise", [(2.0, drop), (20.0, lambda e: e.rehab.state())], seconds=22)
    assert out[20.0]["mode"] == "idle"
    assert out[20.0]["result"]["error"] == "no_band_data" and out[20.0]["calibrated"] == []
    assert r.engine.band_emulator.commands[-1] == "RATE,10"


# ---- recovery service, plan flow, progress -------------------------------------------------------
def svc_with_case(limits=None):
    cfg = make_settings()
    svc = EdgeService(cfg, SimSource("elbow_exercise", speed=0.0), MockSms(), SimIO(), Store(":memory:"))
    p = svc.start_program("recovery", "elbow fracture", None, recovery_case={
        "joint": "elbow", "side": "right", "injury_date": "2026-10-01", "injury_type": "fracture",
        "max_angle_limits": limits or {"elbow_flexion": 120}})
    return svc, p


def test_recovery_program_creates_case_and_validates():
    svc, p = svc_with_case()
    c = svc.recovery.case(p["id"])
    assert c["joint"] == "elbow" and c["limb"] == "arm" and c["max_angle_limits_json"] == {"elbow_flexion": 120.0}
    assert svc.engine.program_id == p["id"]
    for bad in ({}, {"joint": "tail", "side": "left"}, {"joint": "elbow", "side": "up"},
                {"joint": "elbow", "side": "left", "max_angle_limits": {"knee_flexion": 90}}):
        with pytest.raises(ValueError):
            svc.start_program("recovery", "x", None, recovery_case=bad)
    with pytest.raises(ValueError):
        svc.start_program("recovery", "x", None)
    assert svc.active_program()["id"] == p["id"]                  # failed starts did not stop the program


def test_plan_generation_approval_and_progress_with_forecast():
    svc, p = svc_with_case()
    pid, day = p["id"], 86_400_000
    with pytest.raises(LookupError):
        svc.recovery.generate(pid)                                # no ROM test yet
    t0 = int(time.time() * 1000) - 30 * day
    rng = np.random.default_rng(2)
    for d in range(0, 28, 3):
        rom = 130 - 90 * math.exp(-0.09 * d) + rng.normal(0, 1)
        svc.store.upsert("assessments", {"program_id": pid, "ts": t0 + d * day, "kind": "rom",
                                         "movement": "elbow_flexion", "side": "right", "rom": rom,
                                         "min_angle": 0.0, "max_angle": rom, "phase": "baseline"})
    svc.store.upsert("assessments", {"program_id": pid, "ts": t0, "kind": "pain", "pain_0_10": 3, "phase": "baseline"})
    for side, base in (("right", 12), ("left", 28)):
        svc.store.upsert("assessments", {"program_id": pid, "ts": t0, "kind": "grip", "side": side,
                                         "grip_peak": base, "grip_mean": base - 2, "unit": "kg", "phase": "baseline"})
    pl = svc.recovery.generate(pid)
    assert pl["stage"] == 3 and pl["status"] == "waiting_for_doctor" and pl["version"] == 1
    assert all(i["target_angle"] <= 120 for i in pl["items"])     # doctor limit
    pl2 = svc.recovery.generate(pid)
    assert pl2["version"] == 2
    ap = svc.recovery.approve(pid, "Dr. Rao")
    assert ap["status"] == "approved" and ap["approved_by"] == "Dr. Rao"
    prog = svc.recovery.progress(pid)
    assert len(prog["rom"]) == 10 and prog["rom"][0]["pct"] < prog["rom"][-1]["pct"]
    assert prog["grip"]["injured"][0]["peak"] == 12 and prog["grip"]["healthy"][0]["peak"] == 28
    f = prog["forecast"]
    assert f["ok"] and f["est_ts"] and f["ci_low_ts"] <= f["est_ts"] <= f["ci_high_ts"] + 1
    assert prog["target"] == pytest.approx(min(0.9 * 150, 120))
    assert svc.recovery.set_limits(pid, {"elbow_flexion": 100}, True)["doctor_approved"] == 1
    with pytest.raises(ValueError):
        svc.recovery.set_limits(pid, {"knee_flexion": 90}, True)


def test_progress_without_enough_sessions_gives_no_forecast():
    svc, p = svc_with_case()
    assert svc.recovery.progress(p["id"])["forecast"]["ok"] is False
    assert svc.recovery.progress(None) == {"case": None}


# ---- REST API ------------------------------------------------------------------------------------
def api(speed=20.0):
    cfg = make_settings()
    svc = EdgeService(cfg, SimSource("elbow_exercise", speed=speed, loop=False), MockSms(), SimIO(), Store(":memory:"))
    return svc, TestClient(create_app(svc))


def wait_for(cond, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_api_recovery_flow_calibrate_rom_plan_exercise_progress():
    svc, c = api(speed=15.0)
    with c:
        assert c.post("/api/programs", json={"type": "recovery"}).status_code == 422
        r = c.post("/api/programs", json={"type": "recovery", "purpose": "elbow", "recovery_case": {
            "joint": "elbow", "side": "right", "injury_date": "2026-10-20"}})
        assert r.status_code == 201
        assert c.get("/api/recovery").json()["case"]["joint"] == "elbow"
        assert c.post("/api/calibrate", json={"joint": "tail"}).status_code == 422
        e = c.post("/api/assessments/rom/start", json={"movement": "elbow_flexion"})
        assert e.status_code == 409 and e.json()["detail"]["code"] == "not_calibrated"
        cal = c.post("/api/calibrate", json={"joint": "elbow"}).json()
        assert cal["result"]["ok"] and "elbow" in cal["calibrated"]
        assert c.get("/api/exercises", params={"joint": "knee"}).json()[0]["joint"] == "knee"
        assert wait_for(lambda: svc.engine.now >= 37)
        assert c.post("/api/assessments/rom/start", json={"movement": "elbow_flexion", "phase": "baseline",
                                                          "side": "right"}).status_code == 200
        assert c.post("/api/assessments/rom/start", json={"movement": "elbow_flexion"}).status_code == 409
        assert wait_for(lambda: svc.engine.now >= 75)
        rom = c.post("/api/assessments/rom/stop").json()
        assert 95 < rom["rom"] < 120
        assert c.post("/api/assessments/rom/stop").status_code == 409
        assert c.post("/api/assessments/pain", json={"score": 3, "phase": "baseline"}).json()["pain_0_10"] == 3
        assert c.post("/api/assessments/pain", json={"score": 12}).status_code == 422
        pl = c.post("/api/plan/generate").json()
        assert pl["status"] in ("waiting_for_doctor", "may_start_stage1") and pl["items"]
        assert c.get("/api/plan").json()["id"] == pl["id"]
        assert c.post("/api/plan/approve", json={"by": "Dr. Rao"}).json()["status"] == "approved"
        st = c.post("/api/exercise/start", json={"exercise_id": pl["items"][0]["exercise_id"], "item": pl["items"][0],
                                                 "pain_before": 2})
        assert st.status_code == 200 and st.json()["mode"] == "exercise"
        assert c.get("/api/rehab/state").json()["mode"] == "exercise"
        assert wait_for(lambda: svc.engine.rehab.mode == "exercise" and (svc.engine.rehab.state()["reps"] or 0) >= 1, 40) \
            or svc.engine.rehab.mode == "idle"
        if svc.engine.rehab.mode == "exercise":
            s = c.post("/api/exercise/stop", json={"pain_after": 2}).json()
            assert s["kind"] == "session" and s["pain_after"] == 2
        p = c.get("/api/progress").json()
        assert p["case"]["joint"] == "elbow" and p["rom"] and p["forecast"]["ok"] is False
        assert c.get("/api/history", params={"kind": "sessions"}).json()["count"] >= 1
        assert c.get("/api/history", params={"kind": "assessments"}).json()["count"] >= 2


def test_api_grip_assessment_and_limits():
    svc, c = api(speed=20.0)
    with c:
        c.post("/api/programs", json={"type": "recovery", "recovery_case": {"joint": "wrist", "side": "left"}})
        g = c.post("/api/assessments/grip", json={"side": "left"}).json()
        assert g["result"]["ok"] and g["result"]["grip_peak"] > 15
        r = c.put("/api/recovery/limits", json={"limits": {"wrist_flexion": 60}, "approved": True}).json()
        assert r["max_angle_limits_json"] == {"wrist_flexion": 60.0} and r["doctor_approved"] == 1
        assert c.put("/api/recovery/limits", json={"limits": {"knee_flexion": 60}}).status_code == 422

"""Milestone 0: the simulator must produce plausible, scenario-specific data."""
from __future__ import annotations

import asyncio
import functools

import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt
from scipy.spatial.transform import Rotation as R
from senseband_edge.hw.sim import Simulator, list_scenarios
from senseband_edge.hw.sim.analysis import ecg_r_peaks, hr_from_ecg, rr_cv, spo2_from_ppg
from senseband_edge.hw.sim.ble_lines import ble_lines


@functools.cache
def run(name: str, seconds: float | None = None):
    sim = Simulator(name)
    return list(sim.frames(seconds)), sim


def cat(frames, getter):
    return np.concatenate([getter(f) for f in frames])


def window(frames, a, b):
    return [f for f in frames if a <= f.t0 < b]


def ecg(frames):
    return cat(frames, lambda f: f.ecg.mv)


def test_all_scenarios_run_without_nan():
    assert len(list_scenarios()) >= 15
    from senseband_edge.hw.sim.scenario import load_timeline
    for name in list_scenarios():
        if load_timeline(name).duration_s > 700:          # day-long scenarios have their own tests
            continue
        frames, _ = run(name)
        for f in frames:
            arrays = [f.ecg.mv, f.ppg.ir, f.ppg.red]
            for c in f.imu.values():
                arrays += [c.acc_g, c.gyro_dps, c.quat_wxyz]
            assert all(np.isfinite(a).all() for a in arrays), (name, f.t0)
            for c in f.imu.values():  # unit quaternions
                assert np.allclose(np.linalg.norm(c.quat_wxyz, axis=1), 1.0, atol=1e-6)


def test_deterministic_with_seed():
    a = list(Simulator("resting", seed=5).frames(5))
    b = list(Simulator("resting", seed=5).frames(5))
    c = list(Simulator("resting", seed=6).frames(5))
    assert np.array_equal(ecg(a), ecg(b))
    assert not np.array_equal(ecg(a), ecg(c))


def test_sample_counts_exact():
    frames, sim = run("resting", 20)
    assert len(ecg(frames)) == 250 * 20
    assert len(cat(frames, lambda f: f.imu["waist"].acc_g)) == 50 * 20
    assert len(cat(frames, lambda f: f.ppg.ir)) == 25 * 20
    assert sum(len(f.temp) for f in frames) == 4


def test_resting_ecg_hr_and_spo2():
    frames, _ = run("resting", 60)
    assert abs(hr_from_ecg(ecg(frames[25:]), 250) - 72) < 3
    assert 0.0 < rr_cv(ecg(frames[25:]), 250) < 0.08          # regular
    ir, red = cat(frames, lambda f: f.ppg.ir), cat(frames, lambda f: f.ppg.red)
    assert (ir > 50000).all()
    assert abs(spo2_from_ppg(red, ir, 25) - 97) < 2      # SpO2 recoverable from raw PPG
    t = np.array([s.celsius for f in frames for s in f.temp])
    assert 35.8 < t.min() and t.max() < 37.0


@pytest.mark.slow
def test_jogging_10_minutes_plausible():
    """Milestone 0 acceptance: `make sim SCENARIO=jogging` runs 10 min without errors."""
    frames, sim = run("jogging")
    assert sim.duration_s == 600 and len(frames) == 3000
    jog = window(frames, 200, 400)
    walk = window(frames, 70, 90)
    sit = window(frames, 20, 55)
    mag = lambda fs: np.linalg.norm(cat(fs, lambda f: f.imu["waist"].acc_g), axis=1)  # noqa: E731
    assert mag(sit).std() < 0.05
    assert 0.1 < mag(walk).std() < 0.4
    assert 0.5 < mag(jog).std() < 1.5 and mag(jog).max() > 2.0     # running impacts
    # ECG HR tracks truth during jogging despite motion artifact
    for a in (230, 300, 380):
        hr = hr_from_ecg(ecg(window(frames, a - 10, a)), 250)
        assert abs(hr - 155) < 8, (a, hr)
    # cadence ~2.8 steps/s from the vertical axis
    z = cat(jog, lambda f: f.imu["waist"].acc_g[:, 2])
    spec = np.abs(np.fft.rfft(z - z.mean()))
    assert abs(np.fft.rfftfreq(len(z), 1 / 50)[spec.argmax()] - 2.8) < 0.2
    # PPG is corrupted by motion more than ECG (realistic) but never non-finite
    assert cat(jog, lambda f: f.ppg.ir).std() > cat(sit, lambda f: f.ppg.ir).std()


def test_sleeping_has_desaturations_and_lying_posture():
    frames, _ = run("sleeping", 300)
    sp = np.array([f.truth.spo2 for f in frames])
    assert sp.max() >= 95.5 and sp.min() <= 91
    assert {f.truth.posture for f in frames} >= {"lying_supine", "lying_left"}
    acc = cat(window(frames, 5, 25), lambda f: f.imu["waist"].acc_g).mean(axis=0)
    assert acc[0] > 0.9                                    # gravity on x when supine
    assert 50 < hr_from_ecg(ecg(window(frames, 10, 25)), 250) < 62
    ppg = lambda w: (cat(w, lambda f: f.ppg.red), cat(w, lambda f: f.ppg.ir))  # noqa: E731
    dip = spo2_from_ppg(*ppg(window(frames, 41, 49)), 25)    # dip 30..60 s, bottom at 45 s
    base = spo2_from_ppg(*ppg(window(frames, 5, 25)), 25)
    assert dip < base - 3


def test_fall_has_freefall_impact_then_lying_still():
    frames, _ = run("fall", 120)
    ev = [f for f in frames if f.truth.event == "fall"]
    assert len(ev) == 1
    t_fall = ev[0].t0
    acc = cat(window(frames, t_fall - 0.4, t_fall + 2.0), lambda f: f.imu["waist"].acc_g)
    mag = np.linalg.norm(acc, axis=1)
    assert mag.min() < 0.4 and mag.max() > 2.5                # FALL_FREEFALL_G / FALL_IMPACT_G
    before = cat(window(frames, 25, 38), lambda f: f.imu["waist"].acc_g)
    assert np.linalg.norm(before, axis=1).max() < 2.0          # walking never looks like a fall
    after = cat(window(frames, t_fall + 15, t_fall + 40), lambda f: f.imu["waist"].acc_g)
    assert np.linalg.norm(after, axis=1).std() < 0.05          # no movement afterwards
    assert after.mean(axis=0)[0] > 0.9                         # now lying
    assert frames[-1].truth.posture == "lying_supine"


def test_collapse_stops_heartbeat():
    frames, _ = run("collapse", 120)
    assert sum(f.truth.event == "collapse" for f in frames) == 1
    late = ecg(window(frames, 70, 110))
    assert len(ecg_r_peaks(late, 250)) == 0                    # flat line
    ir = cat(window(frames, 70, 110), lambda f: f.ppg.ir)
    sos = butter(2, [0.5, 5.0], btype="band", fs=25, output="sos")
    pulse_rms = lambda x: np.std(sosfiltfilt(sos, x)) / x.mean()  # noqa: E731
    ir_ok = cat(window(frames, 25, 38), lambda f: f.ppg.ir)
    assert pulse_rms(ir) < 0.25 * pulse_rms(ir_ok)             # pulse wave gone
    before = ecg(window(frames, 25, 38))
    assert hr_from_ecg(before, 250) > 100
    mag = np.linalg.norm(cat(window(frames, 40.4, 43), lambda f: f.imu["waist"].acc_g), axis=1)
    assert 1.4 < mag.max() < 2.5                               # slump impact, softer than a fall


def test_low_spo2_ramps_to_86():
    frames, _ = run("low_spo2", 300)
    assert frames[10].truth.spo2 > 96
    assert abs(frames[-1].truth.spo2 - 86) < 0.5
    ir, red = cat(frames[-300:], lambda f: f.ppg.ir), cat(frames[-300:], lambda f: f.ppg.red)
    assert abs(spo2_from_ppg(red, ir, 25) - 86) < 2


def test_atrial_fibrillation_irregular_without_p_waves():
    frames, _ = run("atrial_fibrillation", 300)
    normal, af = ecg(window(frames, 15, 55)), ecg(window(frames, 120, 240))
    assert rr_cv(normal, 250) < 0.08
    assert rr_cv(af, 250) > 0.15
    assert 90 < hr_from_ecg(af, 250) < 125
    assert {f.truth.rhythm for f in frames} == {"normal", "af"}


def test_lead_off_and_finger_off_flags():
    frames, _ = run("lead_off", 300)
    lo = np.concatenate([f.ecg.lead_off for f in frames])
    assert not lo[: 250 * 55].any() and lo[250 * 65: 250 * 115].all() and not lo[250 * 125:].any()
    seg = ecg(window(frames, 70, 110))
    assert len(ecg_r_peaks(seg, 250)) == 0 or np.ptp(seg) < 1.0   # no real QRS
    fo = cat(window(frames, 160, 190), lambda f: f.ppg.finger)
    assert not fo.any()
    assert cat(window(frames, 10, 50), lambda f: f.ppg.finger).all()


def test_noisy_ecg_is_noisier():
    frames, _ = run("noisy_ecg", 240)
    clean, noisy = ecg(window(frames, 20, 55)), ecg(window(frames, 80, 140))
    hf = lambda x: np.std(np.diff(x))  # noqa: E731
    assert hf(noisy) > 3 * hf(clean)


def test_resting_tachycardia_hr_rises_at_rest():
    frames, _ = run("resting_tachycardia", 300)
    assert abs(hr_from_ecg(ecg(window(frames, 100, 118)), 250) - 72) < 4
    assert abs(hr_from_ecg(ecg(window(frames, 250, 290)), 250) - 140) < 6
    mag = np.linalg.norm(cat(window(frames, 250, 290), lambda f: f.imu["waist"].acc_g), axis=1)
    assert mag.std() < 0.05                                  # still sitting


@pytest.mark.parametrize("name,rom,reps", [("elbow_exercise", 110, 10), ("knee_exercise", 95, 8)])
def test_exercise_joint_angle_from_relative_quaternion(name, rom, reps):
    """Relative quaternion of the two limb IMUs must equal the truth hinge angle (M3 basis)."""
    frames, sim = run(name, 120)
    q = lambda key: np.concatenate([f.imu[key].quat_wxyz for f in frames])  # noqa: E731
    qr, qm = q("limb_ref"), q("limb_moving")
    to_r = lambda w: R.from_quat(np.column_stack([w[:, 1:], w[:, 0]]))  # noqa: E731
    rel = (to_r(qr).inv() * to_r(qm)).as_rotvec(degrees=True)
    angle = np.linalg.norm(rel, axis=1)
    truth = np.abs(sim.timeline.joint_angle(np.arange(len(angle)) / 50))
    assert truth.max() > 0.8 * rom
    assert np.abs(angle - truth).max() < 2.0                # quaternion jitter only
    # reps are countable from the angle curve: peaks ~ rom
    from scipy.signal import find_peaks
    pk, _ = find_peaks(angle, height=0.8 * rom, distance=50 * 3)
    assert len(pk) == reps
    assert abs(np.median(angle[pk]) - rom) < 8
    # waist and reference limb stay still while the moving limb rotates
    gref = np.concatenate([f.imu["limb_ref"].gyro_dps for f in frames[300:]])
    assert np.abs(gref).mean() < 2.0


def test_grip_stream_only_during_test():
    frames, _ = run("grip_test", 76)
    assert all(f.grip is None for f in window(frames, 0, 20))
    g = [f.grip for f in frames if f.grip is not None]
    assert g and g[0].fs == 250
    v = np.concatenate([x.volts for x in g])
    assert v.max() > 1.5 and v.min() >= 0.0                # 18 kg ~ 1.7 V on the default cal


def test_ble_lines_match_firmware_protocol():
    frames, _ = run("resting", 10)
    lines = list(ble_lines(frames))
    i = [x for x in lines if x.startswith("I,")]
    v = [x for x in lines if x.startswith("V,")]
    assert 240 <= len(i) <= 260 and len(v) == 10
    kind, ms, pitch, roll = i[0].split(",")
    float(pitch), float(roll), int(ms)
    parts = v[-1].split(",")
    assert len(parts) == 6 and parts[2] == "1" and parts[4] == "1" and parts[5] == "1"
    assert abs(int(parts[1]) - 72) < 6 and abs(int(parts[3]) - 97) < 3
    lo, _ = run("lead_off", 300)
    inv = [x for x in ble_lines(window(lo, 160, 190)) if x.startswith("V,")]
    assert all(x.split(",")[2] == "0" and x.split(",")[5] == "0" for x in inv)


def test_async_stream_realtime_pacing():
    async def go():
        sim = Simulator("resting")
        n = 0
        async for _ in sim.stream(speed=50.0, duration_s=4):
            n += 1
        return n

    assert asyncio.run(go()) == 20


def test_unknown_scenario_message():
    with pytest.raises(FileNotFoundError, match="Available"):
        Simulator("nope")

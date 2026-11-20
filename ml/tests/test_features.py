"""Shared feature package: correctness on simulator data, robustness, and train/serve parity."""
from __future__ import annotations

import numpy as np
import pytest
import senseband_features as F
from senseband_edge.hw.sim import Simulator
from senseband_features import (
    ECG_QUALITY_FEATURES,
    FALL_FEATURES,
    IMU_FEATURES,
    RR_FEATURES,
    ecg_quality_features,
    expected_hr_features,
    fall_window_features,
    imu_window_features,
    r_peaks,
    rr_features,
    rr_intervals,
)


def sim_data(name: str, seconds: float):
    fr = list(Simulator(name).frames(seconds))
    return (np.concatenate([f.imu["waist"].acc_g for f in fr]), np.concatenate([f.ecg.mv for f in fr]))


def col(v: np.ndarray, name: str, names=IMU_FEATURES) -> float:
    return float(v[names.index(name)])


def test_vectors_match_their_name_lists_and_are_finite():
    acc, ecg = sim_data("resting", 20)
    assert imu_window_features(acc[:128]).shape == (len(IMU_FEATURES),)
    assert ecg_quality_features(ecg[:1250]).shape == (len(ECG_QUALITY_FEATURES),)
    assert rr_features(np.array([0.8] * 30)).shape == (len(RR_FEATURES),)
    assert fall_window_features(acc[:200]).shape == (len(FALL_FEATURES),)
    for v in (imu_window_features(acc[:128]), ecg_quality_features(ecg[:1250]), rr_features(rr_intervals(r_peaks(ecg)))):
        assert np.isfinite(v).all() and v.dtype == np.float32
    assert len(set(IMU_FEATURES)) == len(IMU_FEATURES) and len(set(RR_FEATURES)) == len(RR_FEATURES)
    assert F.FEATURE_VERSION >= 2


def test_imu_features_separate_the_activities_and_postures():
    sit, _ = sim_data("resting", 20)
    jog, _ = sim_data("jogging", 200)
    sleep, _ = sim_data("sleeping", 30)
    f_sit, f_jog, f_sleep = (imu_window_features(a[a.shape[0] - 128 - 300:a.shape[0] - 300]) for a in (sit, jog, sleep))
    f_walk = imu_window_features(jog[int(75 * 50):int(75 * 50) + 128])
    f_run = imu_window_features(jog[int(150 * 50):int(150 * 50) + 128])
    assert col(f_sit, "std_norm") < 0.05 < col(f_walk, "std_norm") < col(f_run, "std_norm")
    assert col(f_sit, "tilt_z_deg") > 8 and col(f_sleep, "abs_mean_x") > 0.9 and col(f_sleep, "tilt_z_deg") > 60   # lying
    assert col(f_run, "dom_freq") > col(f_walk, "dom_freq") - 0.3                                                   # cadence shows
    assert col(f_run, "band_3_6") + col(f_run, "band_1_3") > col(f_sit, "band_3_6") + col(f_sit, "band_1_3") - 1e-6


def test_imu_features_reject_bad_windows():
    for bad in (np.zeros((8, 3)), np.zeros((128, 2)), np.zeros(128)):
        with pytest.raises(ValueError):
            imu_window_features(bad)
    assert np.isfinite(imu_window_features(np.zeros((128, 3)))).all()                                                 # flat is safe
    assert np.isfinite(imu_window_features(np.full((128, 3), np.nan))).all()


def test_r_peaks_recover_heart_rate_and_rr_irregularity_separates_af():
    _, normal = sim_data("resting", 60)
    pk = r_peaks(normal[250 * 20:250 * 50])
    rr = rr_intervals(pk)
    assert abs(60 / rr.mean() - 72) < 3 and len(pk) >= 30
    _, af = sim_data("atrial_fibrillation", 200)
    rr_af = rr_intervals(r_peaks(af[250 * 120:250 * 150]))
    fn, fa = rr_features(rr), rr_features(rr_af)
    for name in ("cv_rr", "rmssd", "sampen", "pnn50", "frac_irregular"):
        assert fa[RR_FEATURES.index(name)] > fn[RR_FEATURES.index(name)], name
    assert fn[RR_FEATURES.index("cv_rr")] < 0.08 < 0.15 < fa[RR_FEATURES.index("cv_rr")]
    assert rr_intervals(np.array([0, 10, 5000])).size == 0                                                              # impossible gaps dropped


def test_rr_features_are_safe_for_short_or_odd_input():
    for rr in (np.array([]), np.array([0.8]), np.array([0.8, 0.8, 0.8]), np.full(200, 0.8), np.array([0.8, np.nan, 0.9, 1.0, 0.7])):
        v = rr_features(rr)
        assert v.shape == (len(RR_FEATURES),) and np.isfinite(v).all()
    assert rr_features(np.array([0.8] * 3))[0] == 4                      # n_beats = intervals + 1
    assert r_peaks(np.zeros(2000)).size == 0 and r_peaks(np.full(2000, np.nan)).size == 0 and r_peaks(np.zeros(10)).size == 0


def test_quality_features_flag_noise_flat_and_lead_off():
    _, clean = sim_data("resting", 60)
    _, noisy = sim_data("noisy_ecg", 140)
    _, off = sim_data("lead_off", 100)
    c = ecg_quality_features(clean[250 * 20:250 * 25])
    n = ecg_quality_features(noisy[250 * 90:250 * 95])
    h = ecg_quality_features(off[250 * 70:250 * 75])
    q = ECG_QUALITY_FEATURES.index
    assert c[q("template_corr")] > 0.9 > n[q("template_corr")]
    assert n[q("diff_std_ratio")] > c[q("diff_std_ratio")]
    assert h[q("n_peaks")] <= 3 or h[q("template_corr")] < 0.5            # hum has no QRS pattern
    with pytest.raises(ValueError):
        ecg_quality_features(np.zeros(100))
    assert np.isfinite(ecg_quality_features(np.zeros(1250))).all()


def test_fall_features_distinguish_a_fall_from_jogging_impacts():
    fr = list(Simulator("fall").frames(60))
    acc = np.concatenate([f.imu["waist"].acc_g for f in fr])
    k = int(np.argmax(np.linalg.norm(acc, axis=1)))
    fall = fall_window_features(acc[max(0, k - 100):k + 100])
    jfr = list(Simulator("jogging").frames(300))
    jacc = np.concatenate([f.imu["waist"].acc_g for f in jfr])[int(200 * 50):int(230 * 50)]
    jk = int(np.argmax(np.linalg.norm(jacc, axis=1)))
    jog = fall_window_features(jacc[max(0, jk - 100):jk + 100])
    i = FALL_FEATURES.index
    assert fall[i("peak_g")] > 2.5 and fall[i("min_g_before_peak")] < 0.5 and fall[i("orient_change_deg")] > 45
    assert jog[i("orient_change_deg")] < 20 and jog[i("post_std")] > fall[i("post_std")]


def test_expected_hr_features_encode_activity_time_and_beta_blocker():
    a = expected_hr_features("running", 0.8, 2.8, 0.6, 6.0, 36.5, False)
    b = expected_hr_features("sitting", 0.0, 0.0, 0.0, 6.0, None, True)
    assert a.shape == (len(F.EXPECTED_HR_FEATURES),) and a[0] > b[0] and b[-1] == 1.0 and a[-1] == 0.0
    assert np.isclose(expected_hr_features("sitting", 0, 0, 0, 0)[4:6], [0, 1]).all()          # midnight: sin 0, cos 1
    assert expected_hr_features("nonsense", 0, 0, 0, 12)[0] == F.ACTIVITY_CODES["unknown"]


def test_edge_and_training_share_one_implementation():
    """CLAUDE.md: feature code lives in ONE package used by both (no train/serve skew)."""
    import senseband_edge.models.registry as reg
    import senseband_train.core  # noqa: F401
    import senseband_train.train_models as tm
    assert reg.imu_window_features is F.imu_window_features is tm.imu_window_features
    assert reg.ecg_quality_features is F.ecg_quality_features is tm.ecg_quality_features
    assert reg.rr_features is F.rr_features is tm.rr_features
    assert reg.fall_window_features is F.fall_window_features
    assert reg.expected_hr_features is F.expected_hr_features is tm.expected_hr_features

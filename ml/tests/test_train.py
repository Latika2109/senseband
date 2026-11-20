"""Training utilities, dataset safety, loaders (real data when present) and the metrics-file contract."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import senseband_features as F
from senseband_edge.record import Recorder
from senseband_train import core, datasets
from senseband_train.core import MAX_LATENCY_MS, MAX_MODEL_BYTES, MODELS_DIR


# ---- ONNX export on a toy problem ---------------------------------------------------------------------
def test_lightgbm_to_onnx_parity_size_and_latency(tmp_path):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, 10)).astype(np.float32)
    y = np.where(X[:, 0] + 0.5 * X[:, 1] > 0, "a", np.where(X[:, 2] > 0.5, "b", "c"))
    m = core.make_classifier(n_estimators=60).fit(X, y)
    p = core.export_onnx(m, 10, tmp_path / "m.onnx")
    sess = core.onnx_session(p)
    assert core.verify_parity(m, sess, X[:200]) < 1e-5
    assert p.stat().st_size < MAX_MODEL_BYTES
    assert core.latency_ms(sess, 10, runs=50)["p95"] < MAX_LATENCY_MS
    probs = core.onnx_predict(sess, X[:5])
    assert probs.shape == (5, 3) and np.allclose(probs.sum(axis=1), 1.0, atol=1e-4)
    r = core.make_regressor(n_estimators=60).fit(X, X[:, 0] * 4 + 1)
    p2 = core.export_onnx(r, 10, tmp_path / "r.onnx", regressor=True)
    assert core.verify_parity(r, core.onnx_session(p2), X[:100], regressor=True) < 1e-4


def test_group_folds_never_mix_subjects_and_cv_is_subject_wise():
    groups = np.repeat(np.arange(12), 10)
    for tr, te in core.group_folds(groups, 4):
        assert not set(groups[tr]) & set(groups[te])
    rng = np.random.default_rng(1)
    X = rng.normal(size=(120, 4)).astype(np.float32)
    # a feature that identifies the SUBJECT (not the class) must not look predictive under subject-wise CV
    y = np.array(["a", "b"] * 60)
    X[:, 0] = groups + rng.normal(0, 0.01, 120)
    cv = core.cross_val_classify(X, y, groups, 4, n_estimators=30)
    assert cv["macro_f1"] < 0.75 and cv["n_groups"] == 12 and "GroupKFold" in cv["scheme"]


# ---- archive safety ------------------------------------------------------------------------------------
def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return path


def test_safe_extract_blocks_traversal_absolute_paths_and_bombs(tmp_path):
    ok = make_zip(tmp_path / "ok.zip", {"a/b.txt": b"hi", "c.txt": b"x"})
    out = datasets.safe_extract(ok, tmp_path / "out")
    assert sorted(p.name for p in out) == ["b.txt", "c.txt"] and (tmp_path / "out/a/b.txt").read_bytes() == b"hi"
    for name in ("../evil.txt", "a/../../evil.txt", "/etc/evil.txt"):
        z = make_zip(tmp_path / "bad.zip", {name: b"x"})
        with pytest.raises(ValueError, match="unsafe path"):
            datasets.safe_extract(z, tmp_path / "out2")
    assert not (tmp_path / "evil.txt").exists() and not Path("/etc/evil.txt").exists()
    old = datasets.MAX_EXTRACT_BYTES
    datasets.MAX_EXTRACT_BYTES = 10
    try:
        with pytest.raises(ValueError, match="too large"):
            datasets.safe_extract(make_zip(tmp_path / "big.zip", {"x": b"0" * 100}), tmp_path / "out3")
    finally:
        datasets.MAX_EXTRACT_BYTES = old


def test_align_up_rotates_the_standing_gravity_to_plus_z():
    up = np.array([0.9, 0.3, 0.1])
    acc = np.tile(up, (50, 1))
    out = datasets.align_up(acc, up)
    assert np.allclose(out.mean(axis=0), [0, 0, np.linalg.norm(up)], atol=1e-6)
    assert np.allclose(np.linalg.norm(out, axis=1), np.linalg.norm(acc, axis=1))
    assert np.allclose(datasets.align_up(np.array([[0, 0, 1.0]]), np.array([0, 0, 1.0])), [[0, 0, 1]])
    assert np.allclose(datasets.align_up(np.array([[0, 0, -1.0]]), np.array([0, 0, -1.0])), [[0, 0, 1]])


# ---- loaders on the real datasets (skipped when not downloaded) ----------------------------------------
@pytest.mark.skipif(not (datasets.OUT / "uci_har").exists(), reason="UCI HAR not downloaded (make train)")
def test_uci_har_loader_gives_band_frame_windows():
    x, y, s = datasets.load_uci_har()
    assert x.shape[1:] == (128, 3) and len(set(s.tolist())) == 30 and set(y.tolist()) == {"walking", "stairs", "sitting", "standing", "lying"}
    assert x[y == "standing"].mean(axis=(0, 1))[2] > 0.95                      # +z up when standing
    assert abs(x[y == "lying"].mean(axis=(0, 1))[2]) < 0.4                     # lying: gravity leaves z


@pytest.mark.skipif(not (datasets.OUT / "pamap2").exists(), reason="PAMAP2 not downloaded (make train)")
def test_pamap2_loader_has_running_and_plausible_heart_rate():
    p = datasets.load_pamap2(hop=512)
    assert {"running", "lying", "sitting", "standing", "walking", "stairs", "exercising"} <= set(p["y"].tolist())
    assert len(set(p["subject"].tolist())) == 9
    hr_run, hr_lie = np.nanmean(p["hr"][p["y"] == "running"]), np.nanmean(p["hr"][p["y"] == "lying"])
    assert hr_run > hr_lie + 40


@pytest.mark.skipif(not (datasets.OUT / "cinc2017").exists(), reason="CinC 2017 not downloaded (make train)")
def test_cinc2017_loader_resamples_to_250_hz_and_maps_labels():
    got = list(datasets.iter_cinc2017(limit=12))
    assert len(got) == 12 and {lab for _, lab, _ in got} <= {"normal", "af", "other", "noisy"}
    rid, lab, ecg = got[0]
    assert ecg.ndim == 1 and len(ecg) >= 250 * 9 and np.isfinite(ecg).all() and np.abs(ecg).max() < 20


def test_own_recordings_round_trip_into_training_windows(tmp_path):
    from senseband_edge.hw.sim import Simulator
    rec = Recorder(tmp_path, 600)
    sim = Simulator("jogging")
    frames = list(sim.frames(30))
    rec.start("walking", 0.0)
    for f in frames:
        rec.add(f)
    info = rec.stop()
    x, y, g = datasets.load_recordings(tmp_path)
    assert info["label"] == "walking" and x.shape[1:] == (128, 3) and len(x) == len(y) == len(g) >= 10
    assert set(y.tolist()) == {"walking"} and x.shape[0] == (30 * 50 - 128) // 128 + (1 if (30 * 50 - 128) % 128 else 0) or len(x) > 5
    assert datasets.load_recordings(tmp_path / "missing")[0].shape == (0, 128, 3)


# ---- the metrics.json contract for the trained models --------------------------------------------------
METRICS = MODELS_DIR / "metrics.json"


@pytest.mark.skipif(not METRICS.exists(), reason="models not trained yet (make train)")
def test_every_trained_model_meets_the_edge_contract_and_is_honest_about_its_data():
    m = json.loads(METRICS.read_text())
    assert {"m1_activity", "m2_fall", "m3_ecg_quality", "m4_rhythm", "m5_expected_hr"} <= set(m)
    lists = {"m1_activity": F.IMU_FEATURES, "m2_fall": F.FALL_FEATURES, "m3_ecg_quality": F.ECG_QUALITY_FEATURES,
             "m4_rhythm": F.RR_FEATURES, "m5_expected_hr": F.EXPECTED_HR_FEATURES}
    for name, r in m.items():
        assert r["feature_version"] == F.FEATURE_VERSION, f"{name}: retrain after a feature change"
        assert r["features"] == list(lists[name]), f"{name}: feature order drifted from senseband_features"
        f = MODELS_DIR / r["onnx"]["file"]
        assert f.exists() and f.stat().st_size == r["onnx"]["bytes"] < MAX_MODEL_BYTES
        assert r["onnx"]["parity_max_abs_diff"] < 1e-2
        assert r["latency_ms_this_machine"]["median"] < MAX_LATENCY_MS
        assert r["data"]["provenance"] in ("real", "mixed", "simulated") and r["data"]["sources"]
        t = r["target"]
        assert t["met"] == (t["got"] >= t["value"] if t["metric"] != "mae_bpm_after_personalisation" else t["got"] <= t["value"])
        sess = core.onnx_session(f)
        out = sess.run(None, {"features": np.zeros((1, len(r["features"])), np.float32)})
        assert np.isfinite(np.asarray(out[-1], dtype=float)).all()
    assert m["m2_fall"]["data"]["provenance"] == "simulated" and m["m2_fall"]["enable_on_edge"] is False
    assert m["m1_activity"]["data"]["provenance"] == "real" and m["m4_rhythm"]["data"]["provenance"] == "real"
    assert m["m1_activity"]["cv"]["scheme"].startswith("GroupKFold")
    for k in ("m1_activity", "m4_rhythm"):
        base = next(v for kk, v in m[k].items() if kk.startswith("rule_baseline"))
        got = base["macro_f1"] if isinstance(base, dict) else base
        assert (m[k]["cv"]["macro_f1"] if k == "m1_activity" else m[k]["af_f1"]) > got      # the model must beat the rules


def test_personal_hr_learns_a_persons_offset_and_stays_bounded():
    from senseband_edge.models.personal import PersonalHr
    rng = np.random.default_rng(3)
    p = PersonalHr()
    assert p.correct(100.0) == 100.0 and p.correct(150.0) == 150.0                  # starts as the identity
    for _ in range(600):                                                             # this person runs 14 bpm above the population
        pred = float(rng.choice([65, 75, 90, 130, 160]))
        p.update(pred, pred + 14 + rng.normal(0, 3))
    errs = [abs(p.correct(x) - (x + 14)) for x in (65, 90, 130, 160)]
    assert max(errs) < 5 and p.n == 600
    q = PersonalHr.from_state(json_roundtrip(p.state()))
    assert abs(q.correct(80.0) - p.correct(80.0)) < 1e-9
    wild = PersonalHr()
    for _ in range(200):
        wild.update(80.0, 500.0)                                                     # sensor garbage must not run away
    assert 60 <= wild.correct(80.0) <= 140 and PersonalHr.SLOPE_RANGE[0] <= wild.c[1] <= PersonalHr.SLOPE_RANGE[1]


def json_roundtrip(d):
    return json.loads(json.dumps(d))


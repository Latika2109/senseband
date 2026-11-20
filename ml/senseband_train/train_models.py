"""Train M1-M5 and write ml/models/*.onnx + metrics.json (python -m senseband_train)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from senseband_features import (
    ECG_QUALITY_FEATURES,
    EXPECTED_HR_FEATURES,
    FALL_FEATURES,
    FEATURE_VERSION,
    IMU_FEATURES,
    RR_FEATURES,
    ecg_quality_features,
    expected_hr_features,
    imu_window_features,
    r_peaks,
    rr_features,
    rr_intervals,
)
from senseband_features.common import ECG_FS

from . import core, datasets
from .core import MODELS_DIR

CACHE = datasets.ROOT / "cache"


def _cache(name: str, build):  # noqa: ANN001, ANN202
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{name}.npz"
    if p.exists():
        z = np.load(p, allow_pickle=True)
        return {k: z[k] for k in z.files}
    d = build()
    np.savez_compressed(p, **d)
    return d


def _f1_target(metric: str, value: float, got: float, real: bool) -> dict[str, Any]:
    return {"metric": metric, "value": value, "got": round(float(got), 4), "met": bool(got >= value),
            "validated_on_real_data": real}


# ------------------------------------------------------------------------------------------------- M1
def train_m1(out: Path = MODELS_DIR, quick: bool = False) -> dict[str, Any]:
    """Activity & posture: UCI HAR (waist phone, 30 people) + PAMAP2 chest IMU (9 people, incl. running)."""
    def build() -> dict[str, np.ndarray]:
        hx, hy, hs = datasets.load_uci_har()
        p = datasets.load_pamap2(hop=256 if quick else 128)
        wins = np.concatenate([hx, p["x"]])
        feats = np.stack([imu_window_features(w) for w in wins])
        labels = np.concatenate([hy, p["y"]])
        groups = np.concatenate([[f"har{s}" for s in hs], [f"pamap{s}" for s in p["subject"]]])
        src = np.array(["uci_har"] * len(hx) + ["pamap2"] * len(p["x"]))
        return {"X": feats, "y": labels, "g": groups, "src": src}
    d = _cache("m1_quick" if quick else "m1", build)
    X, y, g = d["X"], d["y"].astype(str), d["g"].astype(str)
    cv = core.cross_val_classify(X, y, g, 5)
    base = _rule_baseline_m1(X, y)
    data = {"sources": ["UCI HAR (waist smartphone, 30 subjects, 50 Hz)", "PAMAP2 (chest IMU, 9 subjects, 100->50 Hz)"],
            "provenance": "real", "n_windows": int(len(y)), "n_subjects": int(len(set(g.tolist()))),
            "window": "128 samples @ 50 Hz = 2.56 s",
            "class_counts": {c: int((y == c).sum()) for c in sorted(set(y.tolist()))},
            "notes": "Subject-wise CV. 'running' and 'exercising' come only from PAMAP2 (9 people); no separate "
                     "hold-out dataset. Sleeping is a rule on top (lying + still >= 2 min), not modelled."}
    rep = core.finish_classifier("m1_activity", X, y, cv, IMU_FEATURES, data,
                                 _f1_target("macro_f1", 0.90, cv["macro_f1"], True), out,
                                 extra={"rule_baseline_on_same_windows": base})
    return rep


def _rule_baseline_m1(X: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """The edge's rule-based classifier evaluated on the same feature rows (it only needs std / gravity)."""
    from sklearn.metrics import f1_score
    ix = {n: i for i, n in enumerate(IMU_FEATURES)}
    pred = []
    for r in X:
        lying = max(abs(r[ix["mean_x"]]), abs(r[ix["mean_y"]])) / max(r[ix["mean_norm"]], 1e-6) > 0.7
        std = r[ix["std_norm"]]
        if lying:
            pred.append("lying")
        elif std > 0.08:
            pred.append("running" if std > 0.45 else "walking")
        else:
            pred.append("sitting" if r[ix["tilt_z_deg"]] > 8 else "standing")
    truth = np.where(y == "stairs", "walking", y)
    keep = y != "exercising"
    labels = sorted(set(truth[keep].tolist()))
    return {"macro_f1": float(f1_score(truth[keep], np.array(pred)[keep], average="macro", labels=labels, zero_division=0)),
            "note": "stairs scored as walking, exercising excluded; compare with the model's own macro_f1 above"}


# ------------------------------------------------------------------------------------------------- M2
def train_m2(out: Path = MODELS_DIR, quick: bool = False) -> dict[str, Any]:
    """Fall detection. SisFall was unreachable, so this model is trained on SIMULATED falls and flagged as such."""
    from . import sim_data
    n_pos, n_neg = (60, 180) if quick else (240, 720)
    d = _cache("m2_quick" if quick else "m2", lambda: dict(zip(("X", "y", "g"), sim_data.fall_dataset(n_pos, n_neg), strict=True)))
    X, y, g = d["X"], d["y"].astype(str), d["g"]
    cv = core.cross_val_classify(X, y, g, 5)
    pos = y == "fall"
    from sklearn.metrics import recall_score
    sens = float(recall_score(y == "fall", cv["oof_pred"] == "fall"))
    spec = float(recall_score(y != "fall", cv["oof_pred"] != "fall"))
    data = {"sources": ["edge hardware simulator (randomised fall direction/force + synthetic ADL impacts)"],
            "provenance": "simulated", "n_windows": int(len(y)), "n_falls": int(pos.sum()),
            "notes": "NOT validated on real falls (SisFall/MobiFall not obtainable here). Do not enable on a person "
                     "until retrained and validated on real data: config models.fall.enabled stays false."}
    tgt = {"metric": "sensitivity", "value": 0.95, "got": round(sens, 4), "met": sens >= 0.95,
           "validated_on_real_data": False}
    return core.finish_classifier("m2_fall", X, y, cv, FALL_FEATURES, data, tgt, out,
                                  extra={"sensitivity": sens, "specificity": spec, "enable_on_edge": False})


# ------------------------------------------------------------------------------------------------- M3/M4
def _cinc(quick: bool) -> dict[str, np.ndarray]:
    limit = 500 if quick else None

    def build() -> dict[str, np.ndarray]:
        rr_X, rr_y, rec, q_X, q_y, q_g = [], [], [], [], [], []
        for rid, lab, ecg in datasets.iter_cinc2017(limit=limit):
            seg = ecg[: int(30 * ECG_FS)]
            rr_X.append(rr_features(rr_intervals(r_peaks(seg, ECG_FS), ECG_FS)))
            rr_y.append(lab)
            rec.append(rid)
            for start in (2, 12):
                w = ecg[int(start * ECG_FS):int((start + 5) * ECG_FS)]
                if len(w) >= int(4.5 * ECG_FS):
                    q_X.append(ecg_quality_features(w, ECG_FS))
                    q_y.append("noisy" if lab == "noisy" else "good")
                    q_g.append(rid)
        return {"rr_X": np.stack(rr_X), "rr_y": np.array(rr_y), "rec": np.array(rec), "q_X": np.stack(q_X),
                "q_y": np.array(q_y), "q_g": np.array(q_g)}
    return _cache("cinc_quick" if quick else "cinc", build)


def train_m3(out: Path = MODELS_DIR, quick: bool = False) -> dict[str, Any]:
    """ECG signal quality: real CinC-2017 'noisy' vs readable windows, plus simulated 'unusable' (flat/hum/clipped)."""
    from . import sim_data
    d = _cinc(quick)
    bad = sim_data.unusable_ecg_windows(120 if quick else 600)
    ub = np.stack([ecg_quality_features(w, ECG_FS) for w in bad])
    X = np.concatenate([d["q_X"], ub])
    y = np.concatenate([d["q_y"].astype(str), np.array(["unusable"] * len(ub))])
    g = np.concatenate([d["q_g"].astype(str), np.array([f"sim{i}" for i in range(len(ub))])])
    cv = core.cross_val_classify(X, y, g, 5)
    data = {"sources": ["PhysioNet/CinC 2017 (noisy '~' vs N/A/O recordings, 5 s windows, resampled to 250 Hz)",
                        "simulated unusable windows (flat, 50 Hz hum, clipped, white noise, drift)"],
            "provenance": "mixed", "n_windows": int(len(y)), "class_counts": {c: int((y == c).sum()) for c in sorted(set(y.tolist()))},
            "notes": "CinC 2011 (the dataset named in docs/04) was not reachable; 2017's noisy class is the real-data "
                     "proxy. 'noisy' labels are per recording, so some windows labelled noisy are locally clean."}
    return core.finish_classifier("m3_ecg_quality", X, y, cv, ECG_QUALITY_FEATURES, data,
                                  _f1_target("accuracy", 0.92, cv["accuracy"], True), out)


def train_m4(out: Path = MODELS_DIR, quick: bool = False) -> dict[str, Any]:
    """Rhythm screening on RR features: normal / possible AF / other / noisy (CinC 2017, 30 s)."""
    from sklearn.metrics import f1_score
    d = _cinc(quick)
    X, y, g = d["rr_X"], d["rr_y"].astype(str), d["rec"].astype(str)
    cv = core.cross_val_classify(X, y, g, 5)
    oof = cv["oof_pred"]
    af_f1 = float(f1_score(y == "af", oof == "af"))
    ix = RR_FEATURES.index("cv_rr")
    base_af = float(f1_score(y == "af", X[:, ix] > 0.12))              # the edge's rule: RR variability > 0.12
    data = {"sources": ["PhysioNet/CinC 2017 training set (8,528 single-lead recordings, first 30 s)"],
            "provenance": "real", "n_windows": int(len(y)), "class_counts": {c: int((y == c).sum()) for c in sorted(set(y.tolist()))},
            "notes": "Folds are by recording (CinC does not publish subject ids; most recordings are different people). "
                     "'af' is a screening label 'possible AF', never a diagnosis."}
    tgt = {"metric": "af_f1", "value": 0.80, "got": round(af_f1, 4), "met": af_f1 >= 0.80, "validated_on_real_data": True}
    return core.finish_classifier("m4_rhythm", X, y, cv, RR_FEATURES, data, tgt, out,
                                  extra={"af_f1": af_f1, "rule_baseline_af_f1_rr_cv_gt_0.12": base_af})


# ------------------------------------------------------------------------------------------------- M5
def _m5_rows(p: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """One feature row per PAMAP2 window with the exertion history of the previous 5 minutes."""
    feats = np.stack([imu_window_features(w) for w in p["x"]])
    ix = {n: i for i, n in enumerate(IMU_FEATURES)}
    inten = np.clip(feats[:, ix["std_norm"]], 0, 1)
    cad = feats[:, ix["dom_freq"]]
    X, keep = [], []
    for i in range(len(p["y"])):
        same = (p["subject"] == p["subject"][i]) & (p["t"] < p["t"][i]) & (p["t"] >= p["t"][i] - 300)
        load = float(inten[same].mean()) if same.any() else float(inten[i])
        X.append(expected_hr_features(str(p["y"][i]), float(inten[i]), float(cad[i]), load, 12.0))
        keep.append(np.isfinite(p["hr"][i]))
    keep_a = np.array(keep)
    return np.stack(X)[keep_a], p["hr"][keep_a], p["subject"][keep_a], p["t"][keep_a]


def train_m5(out: Path = MODELS_DIR, quick: bool = False) -> dict[str, Any]:
    """Expected HR: gradient-boosted population prior (PAMAP2) + per-user recursive-least-squares correction."""
    from senseband_edge.models.personal import PersonalHr  # the SAME class the band runs
    d = _cache("m5_quick" if quick else "m5", lambda: dict(zip(("X", "hr", "s", "t"), _m5_rows(datasets.load_pamap2(hop=256 if quick else 128)), strict=True)))
    X, hr, s, t = d["X"], d["hr"], d["s"], d["t"]
    pop_err, rls_err = {}, {}
    for subj in sorted(set(s.tolist())):
        te, tr = s == subj, s != subj
        m = core.make_regressor(n_estimators=150).fit(X[tr], hr[tr])
        order = np.argsort(t[te])
        pred = m.predict(X[te][order])
        truth = hr[te][order]
        n_warm = len(truth) // 4                                    # "learning period": first quarter, no scoring
        rls = PersonalHr()
        errs_p, errs_r = [], []
        for i, (pr, tru) in enumerate(zip(pred, truth, strict=True)):
            corrected = rls.correct(float(pr))
            if i >= n_warm:
                errs_p.append(abs(pr - tru))
                errs_r.append(abs(corrected - tru))
            rls.update(float(pr), float(tru))
        pop_err[str(subj)], rls_err[str(subj)] = float(np.mean(errs_p)), float(np.mean(errs_r))
    mae_pop, mae_rls = float(np.mean(list(pop_err.values()))), float(np.mean(list(rls_err.values())))
    final = core.make_regressor(n_estimators=150).fit(X, hr)
    path = core.export_onnx(final, X.shape[1], out / "m5_expected_hr.onnx", regressor=True)
    sess = core.onnx_session(path)
    diff = core.verify_parity(final, sess, X[:300], regressor=True)
    assert diff < 1e-2 and path.stat().st_size < core.MAX_MODEL_BYTES
    rep = {"name": "m5_expected_hr", "algorithm": "lightgbm regressor + RLS personalisation", "feature_version": FEATURE_VERSION,
           "features": EXPECTED_HR_FEATURES, "data": {"sources": ["PAMAP2 (9 subjects, chest HR strap + IMU)"], "provenance": "real",
                                                      "n_windows": int(len(hr)), "notes": "Leave-one-subject-out. Time of day and temperature are constant in PAMAP2 so those inputs are untrained."},
           "cv": {"scheme": "leave-one-subject-out; RLS evaluated prequentially after a 25% learning period",
                  "mae_population_bpm": mae_pop, "mae_personalised_bpm": mae_rls, "per_subject_population": pop_err,
                  "per_subject_personalised": rls_err},
           "target": {"metric": "mae_bpm_after_personalisation", "value": 8.0, "got": round(mae_rls, 2), "met": mae_rls <= 8.0,
                      "validated_on_real_data": True},
           "onnx": {"file": path.name, "bytes": path.stat().st_size, "parity_max_abs_diff": diff},
           "latency_ms_this_machine": core.latency_ms(sess, X.shape[1]), "latency_budget_ms": core.MAX_LATENCY_MS,
           "residual_sd_bpm": float(np.std(final.predict(X) - hr))}
    core.write_report("m5_expected_hr", rep, out)
    return rep


ALL = {"m1": train_m1, "m2": train_m2, "m3": train_m3, "m4": train_m4, "m5": train_m5}


def run(names: list[str], out: Path = MODELS_DIR, quick: bool = False) -> dict[str, dict[str, Any]]:
    res = {}
    for n in names:
        t0 = time.time()
        res[n] = ALL[n](out, quick)
        print(f"{n}: done in {time.time() - t0:.0f}s -> {res[n]['target']}", flush=True)
    return res

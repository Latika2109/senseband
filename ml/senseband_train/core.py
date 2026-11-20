"""Training utilities: LightGBM, subject-wise cross-validation, ONNX export + parity, latency, metrics."""
from __future__ import annotations

import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
MAX_MODEL_BYTES = 5 * 1024 * 1024        # CLAUDE.md: models < 5 MB each
MAX_LATENCY_MS = 50.0                    # CLAUDE.md: inference < 50 ms per window (on the Pi 3)

CLASSIFIER_PARAMS: dict[str, Any] = dict(
    n_estimators=200, learning_rate=0.06, num_leaves=24, min_child_samples=15, subsample=0.8,
    subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, n_jobs=4, verbose=-1, random_state=0,
)


def make_classifier(**over: Any):  # noqa: ANN201
    from lightgbm import LGBMClassifier
    return LGBMClassifier(**{**CLASSIFIER_PARAMS, "class_weight": "balanced", **over})


def make_regressor(**over: Any):  # noqa: ANN201
    from lightgbm import LGBMRegressor
    return LGBMRegressor(**{**CLASSIFIER_PARAMS, **over})


def group_folds(groups: np.ndarray, n_splits: int = 5):  # noqa: ANN201
    """Subject-wise folds: no person ever appears in both train and test."""
    from sklearn.model_selection import GroupKFold
    n = min(n_splits, len(set(groups.tolist())))
    return list(GroupKFold(n_splits=n).split(np.zeros(len(groups)), groups=groups))


def cross_val_classify(X: np.ndarray, y: np.ndarray, groups: np.ndarray, n_splits: int = 5,
                       **over: Any) -> dict[str, Any]:
    """Out-of-fold predictions with subject-wise folds -> honest generalisation metrics."""
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
    classes = sorted(set(y.tolist()))
    pred = np.empty(len(y), dtype=object)
    fold_f1 = []
    for tr, te in group_folds(groups, n_splits):
        m = make_classifier(**over).fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
        fold_f1.append(float(f1_score(y[te], pred[te], average="macro", labels=classes, zero_division=0)))
    f1s = f1_score(y, pred, average=None, labels=classes, zero_division=0)
    return {"scheme": f"GroupKFold({n_splits}) by subject/recording", "n": int(len(y)),
            "n_groups": int(len(set(groups.tolist()))),
            "macro_f1": float(f1_score(y, pred, average="macro", labels=classes, zero_division=0)),
            "accuracy": float(accuracy_score(y, pred)),
            "per_class_f1": {c: float(v) for c, v in zip(classes, f1s, strict=True)},
            "confusion": {"labels": classes, "matrix": confusion_matrix(y, pred, labels=classes).tolist()},
            "fold_macro_f1": fold_f1, "oof_pred": pred}


def export_onnx(model: Any, n_features: int, path: Path, regressor: bool = False) -> Path:
    from onnxmltools import convert_lightgbm
    from onnxmltools.convert.common.data_types import FloatTensorType
    kw = {} if regressor else {"zipmap": False}
    onx = convert_lightgbm(model, initial_types=[("features", FloatTensorType([None, n_features]))],
                           target_opset=14, **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(onx.SerializeToString())
    return path


def onnx_session(path: Path):  # noqa: ANN201
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = so.inter_op_num_threads = 1       # the Pi has few cores; keep it predictable
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


def onnx_predict(sess: Any, X: np.ndarray) -> np.ndarray:
    """Class probabilities (n, k) or regression values (n,)."""
    out = sess.run(None, {"features": X.astype(np.float32)})
    return np.asarray(out[1] if len(out) > 1 else out[0])


def verify_parity(model: Any, sess: Any, X: np.ndarray, regressor: bool = False) -> float:
    ref = model.predict(X) if regressor else model.predict_proba(X)
    got = onnx_predict(sess, X).reshape(np.shape(ref))
    return float(np.max(np.abs(np.asarray(ref, dtype=np.float64) - got)))


def latency_ms(sess: Any, n_features: int, runs: int = 300) -> dict[str, float]:
    x = np.random.default_rng(0).normal(size=(1, n_features)).astype(np.float32)
    for _ in range(20):
        sess.run(None, {"features": x})
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        sess.run(None, {"features": x})
        ts.append((time.perf_counter() - t) * 1000)
    return {"median": round(float(np.median(ts)), 3), "p95": round(float(np.percentile(ts, 95)), 3)}


def libs() -> dict[str, str]:
    import lightgbm
    import onnxruntime
    import sklearn
    return {"python": platform.python_version(), "lightgbm": lightgbm.__version__, "sklearn": sklearn.__version__,
            "onnxruntime": onnxruntime.__version__, "numpy": np.__version__}


def write_report(name: str, report: dict[str, Any], out_dir: Path = MODELS_DIR) -> None:
    """Merge one model's report into metrics.json (other models' entries are kept)."""
    path = out_dir / "metrics.json"
    allm = json.loads(path.read_text()) if path.exists() else {}
    report = {k: v for k, v in report.items()}
    report["trained_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    report["libs"] = libs()
    allm[name] = report
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(allm, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def finish_classifier(name: str, X: np.ndarray, y: np.ndarray, cv: dict[str, Any], features: list[str], data: dict,
                      target: dict[str, Any], out_dir: Path = MODELS_DIR, extra: dict | None = None) -> dict[str, Any]:
    """Fit on everything, export ONNX, verify parity + size + latency, write the metrics entry."""
    from . import FEATURE_VERSION  # noqa: PLC0415
    model = make_classifier().fit(X, y)
    path = export_onnx(model, X.shape[1], out_dir / f"{name}.onnx")
    sess = onnx_session(path)
    idx = np.random.default_rng(0).choice(len(X), size=min(500, len(X)), replace=False)
    diff = verify_parity(model, sess, X[idx])
    size, lat = path.stat().st_size, latency_ms(sess, X.shape[1])
    assert diff < 1e-3, f"{name}: ONNX output differs from LightGBM by {diff}"
    assert size < MAX_MODEL_BYTES, f"{name}: {size} bytes exceeds the 5 MB budget"
    cv = {k: v for k, v in cv.items() if k != "oof_pred"}
    rep = {"name": name, "algorithm": "lightgbm", "feature_version": FEATURE_VERSION, "features": features,
           "classes": [str(c) for c in model.classes_], "data": data, "cv": cv, "target": target,
           "onnx": {"file": path.name, "bytes": size, "parity_max_abs_diff": diff},
           "latency_ms_this_machine": lat, "latency_budget_ms": MAX_LATENCY_MS, **(extra or {})}
    write_report(name, rep, out_dir)
    return rep

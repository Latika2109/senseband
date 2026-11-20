"""ONNX model registry with rule-based fallbacks (docs/04: every model has a fallback).

A model is used only if: onnxruntime is installed, the file exists, its metrics entry matches the shared
feature version and class list, it is enabled in config (the fall model is OFF by default: simulated data only),
and it keeps inference under the latency budget. Anything else -> the caller's rule-based fallback runs.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
from senseband_features import (
    FEATURE_VERSION,
    ecg_quality_features,
    expected_hr_features,
    fall_window_features,
    imu_window_features,
    rr_features,
)

from ..config import ModelsCfg

log = logging.getLogger("senseband.models")
REPO = Path(__file__).resolve().parents[3]


def resolve_dir(d: str) -> Path:
    p = Path(d)
    return p if p.is_absolute() else REPO / p


class OnnxModel:
    """One ONNX file. `predict` returns probabilities (classifier) or a float (regressor), or None if unusable."""

    def __init__(self, path: Path, meta: dict[str, Any], budget_ms: float, regressor: bool = False):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.meta, self.budget, self.regressor = meta, budget_ms, regressor
        self.classes: list[str] = [str(c) for c in meta.get("classes", [])]
        self.n_features = len(meta["features"])
        self.ok = True
        self.calls, self.total_ms = 0, 0.0

    def run(self, feats: np.ndarray) -> np.ndarray | None:
        if not self.ok:
            return None
        x = np.asarray(feats, dtype=np.float32).reshape(1, -1)
        if x.shape[1] != self.n_features:
            self.ok = False
            log.error("model feature mismatch: got %d, want %d", x.shape[1], self.n_features)
            return None
        t0 = time.perf_counter()
        try:
            out = self.sess.run(None, {"features": x})
        except Exception:  # noqa: BLE001 - never let a model break monitoring
            self.ok = False
            log.exception("model inference failed; falling back to rules")
            return None
        ms = (time.perf_counter() - t0) * 1000
        self.calls += 1
        self.total_ms += ms
        if self.calls >= 20 and self.total_ms / self.calls > self.budget:
            self.ok = False
            log.warning("model too slow (%.1f ms avg > %.0f ms): switched off", self.total_ms / self.calls, self.budget)
        return np.asarray(out[1] if len(out) > 1 else out[0])

    def proba(self, feats: np.ndarray) -> dict[str, float] | None:
        out = self.run(feats)
        return None if out is None else dict(zip(self.classes, (float(v) for v in out.reshape(-1)), strict=True))

    def value(self, feats: np.ndarray) -> float | None:
        out = self.run(feats)
        return None if out is None else float(out.reshape(-1)[0])


class ModelRegistry:
    def __init__(self, cfg: ModelsCfg):
        self.cfg = cfg
        self.dir = resolve_dir(cfg.dir)
        self.models: dict[str, OnnxModel] = {}
        self.reasons: dict[str, str] = {}
        self.metrics: dict[str, Any] = {}
        if not cfg.enabled:
            self.reasons["*"] = "disabled in config"
            return
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            self.reasons["*"] = "onnxruntime not installed"
            return
        mp = self.dir / "metrics.json"
        if not mp.exists():
            self.reasons["*"] = f"no {mp}"
            return
        self.metrics = json.loads(mp.read_text())
        table = (("activity", "m1_activity", cfg.activity.enabled), ("fall", "m2_fall", cfg.fall.enabled),
                 ("ecg_quality", "m3_ecg_quality", cfg.ecg_quality.enabled),
                 ("rhythm", "m4_rhythm", cfg.rhythm.enabled),
                 ("expected_hr", "m5_expected_hr", cfg.expected_hr.enabled))
        for key, name, on in table:
            self._load(key, name, on)

    def _load(self, key: str, name: str, enabled: bool) -> None:
        if not enabled:
            self.reasons[key] = "disabled in config"
            return
        meta, path = self.metrics.get(name), self.dir / f"{name}.onnx"
        if meta is None or not path.exists():
            self.reasons[key] = "model file or metrics missing"
        elif meta.get("feature_version") != FEATURE_VERSION:
            self.reasons[key] = f"feature version {meta.get('feature_version')} != {FEATURE_VERSION}: retrain"
        elif meta.get("onnx", {}).get("bytes", 0) > 5 * 1024 * 1024:
            self.reasons[key] = "model larger than 5 MB budget"
        else:
            try:
                self.models[key] = OnnxModel(path, meta, self.cfg.max_infer_ms, regressor=key == "expected_hr")
            except Exception as e:  # noqa: BLE001
                self.reasons[key] = f"load failed: {e}"

    def get(self, key: str) -> OnnxModel | None:
        m = self.models.get(key)
        return m if m is not None and m.ok else None

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k in ("activity", "fall", "ecg_quality", "rhythm", "expected_hr"):
            m = self.models.get(k)
            out[k] = ({"active": m.ok, "avg_ms": round(m.total_ms / m.calls, 2) if m.calls else None, "calls": m.calls}
                      if m else {"active": False, "reason": self.reasons.get(k, self.reasons.get("*", "unavailable"))})
        return out


# ---- thin typed helpers: features come ONLY from the shared package ---------------------------------------
def activity_probs(reg: ModelRegistry, acc_window: np.ndarray) -> dict[str, float] | None:
    m = reg.get("activity")
    return None if m is None else m.proba(imu_window_features(acc_window))


def fall_prob(reg: ModelRegistry, acc_window: np.ndarray) -> float | None:
    m = reg.get("fall")
    p = None if m is None else m.proba(fall_window_features(acc_window))
    return None if p is None else p.get("fall", 0.0)


def ecg_quality_probs(reg: ModelRegistry, ecg_5s: np.ndarray, fs: int) -> dict[str, float] | None:
    m = reg.get("ecg_quality")
    return None if m is None else m.proba(ecg_quality_features(ecg_5s, fs))


def rhythm_probs(reg: ModelRegistry, rr_s: np.ndarray) -> dict[str, float] | None:
    m = reg.get("rhythm")
    return None if m is None else m.proba(rr_features(rr_s))


def expected_hr_value(reg: ModelRegistry, activity: str, intensity: float, cadence: float, load5: float,
                      hour: float, temp: float | None, beta: bool) -> float | None:
    m = reg.get("expected_hr")
    return None if m is None else m.value(expected_hr_features(activity, intensity, cadence, load5, hour, temp, beta))

"""Activity + posture from the waist IMU (rule-based fallback for model M1)."""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..config import ActivityCfg

RESTING = ("lying", "sleeping", "sitting", "standing")


@dataclass
class ActivityResult:
    activity: str = "unknown"      # lying | sleeping | sitting | standing | walking | running
    posture: str = "unknown"       # lying | sitting | standing
    intensity: float = 0.0         # 0..1
    std_g: float = 0.0
    cadence_hz: float = 0.0
    lying_s: float = 0.0
    moving: bool = False
    source: str = "rules"          # "rules" | "model"
    prob: float | None = None

    @property
    def resting(self) -> bool:
        return self.activity in RESTING


class ActivityClassifier:
    def __init__(self, fs: int, cfg: ActivityCfg,
                 model_fn: Callable[[np.ndarray], dict[str, float] | None] | None = None, min_prob: float = 0.55):
        self.fs, self.c = fs, cfg
        self.model_fn, self.min_prob = model_fn, min_prob
        self.n = int(cfg.window_s * fs)
        self.buf: deque[np.ndarray] = deque(maxlen=self.n)
        self.hop = int(cfg.hop_s * fs)
        self._since = 0
        self.result = ActivityResult()
        self._lying_since: float | None = None

    def add(self, acc_g: np.ndarray, t_end: float) -> ActivityResult:
        for row in acc_g:
            self.buf.append(row)
        self._since += len(acc_g)
        if len(self.buf) >= self.n and self._since >= self.hop:
            self._since = 0
            self.result = self._classify(np.asarray(self.buf), t_end)
        return self.result

    def _classify(self, w: np.ndarray, now: float) -> ActivityResult:
        c = self.c
        mag = np.linalg.norm(w, axis=1)
        std = float(np.std(mag))
        g = w.mean(axis=0)
        norm = float(np.linalg.norm(g)) or 1.0
        if max(abs(g[0]), abs(g[1])) / norm > c.lying_axis_g:
            posture = "lying"
        else:
            tilt = math.degrees(math.acos(max(-1.0, min(1.0, g[2] / norm))))
            posture = "sitting" if tilt > c.sitting_tilt_deg else "standing"
        spec = np.abs(np.fft.rfft(mag - mag.mean()))
        freqs = np.fft.rfftfreq(len(mag), 1 / self.fs)
        band = (freqs > 1.0) & (freqs < 4.0)
        cadence = float(freqs[band][spec[band].argmax()]) if band.any() and spec[band].max() > 0 \
            else 0.0
        moving = std > c.rest_std_g
        if posture == "lying":
            self._lying_since = self._lying_since if self._lying_since is not None else now
        else:
            self._lying_since = None
        lying_s = 0.0 if self._lying_since is None else now - self._lying_since
        if moving and posture != "lying":
            act = "running" if std > c.run_std_g or (cadence > c.cadence_run_hz and std > 0.25) \
                else "walking"
        elif posture == "lying":
            act = "sleeping" if (not moving and lying_s >= c.sleep_after_lying_s) else "lying"
        else:
            act = posture
        source, prob = "rules", None
        probs = self.model_fn(w) if self.model_fn is not None else None
        if probs:
            label, prob = max(probs.items(), key=lambda kv: kv[1])
            # high raw movement energy is running whatever the model says (little real running data in training)
            if prob >= self.min_prob and act != "running":
                source = "model"
                posture = "lying" if label == "lying" else ("sitting" if label == "sitting" else
                          "standing" if label == "standing" else (posture if posture != "lying" else "standing"))
                act = label
                if label == "lying":
                    self._lying_since = self._lying_since if self._lying_since is not None else now
                    lying_s = now - self._lying_since
                    if not moving and lying_s >= c.sleep_after_lying_s:
                        act = "sleeping"
                moving = label not in ("lying", "sitting", "standing")
        return ActivityResult(act, posture, float(min(1.0, std / 1.0)), std, cadence, lying_s, moving, source,
                              None if prob is None else round(float(prob), 3))

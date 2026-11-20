"""Fall-vs-ADL features for a 4 s waist-IMU window centred on an impact trigger (model M2)."""
from __future__ import annotations

import numpy as np

from .common import EPS, FALL_WINDOW_S, IMU_FS

FALL_FEATURES = [
    "peak_g", "peak_time_s", "min_g_before_peak", "freefall_s", "impact_width_s", "jerk_peak",
    "pre_std", "post_std", "post_mean_dev", "post_std_ratio", "orient_change_deg", "z_drop",
    "energy_post", "n_over_2g",
]


def fall_window_features(acc_g: np.ndarray, fs: float = IMU_FS) -> np.ndarray:
    """acc_g: (n, 3) in g, ~FALL_WINDOW_S long with the trigger near the middle. Returns (len(FALL_FEATURES),)."""
    a = np.asarray(acc_g, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] != 3 or len(a) < int(fs):
        raise ValueError("expected an (n>=fs, 3) accelerometer window")
    n = len(a)
    norm = np.linalg.norm(a, axis=1)
    k = int(np.argmax(norm))                                   # impact = strongest sample in the window
    t_peak = k / fs
    before = norm[max(0, k - int(1.0 * fs)):k] if k > 0 else norm[:1]
    ff = float(before.min()) if len(before) else 1.0
    ff_len = float(np.sum(before < 0.5) / fs) if len(before) else 0.0
    width = float(np.sum(norm[max(0, k - int(0.5 * fs)):k + int(0.5 * fs)] > 0.5 * (norm[k] + 1.0)) / fs)
    jerk = float(np.max(np.linalg.norm(np.diff(a, axis=0), axis=1)) * fs) if n > 1 else 0.0
    pre = norm[:max(1, k - int(0.5 * fs))]
    post = norm[min(n - 1, k + int(0.5 * fs)):]
    if len(post) < 3:
        post = norm[-3:]
    g_pre = a[max(0, k - int(1.5 * fs)):max(1, k - int(0.5 * fs))].mean(axis=0)
    g_post = a[min(n - 1, k + int(0.5 * fs)):].mean(axis=0) if k + int(0.5 * fs) < n else a[-3:].mean(axis=0)
    cosang = float(np.dot(g_pre, g_post) / (np.linalg.norm(g_pre) * np.linalg.norm(g_post) + EPS))
    orient = float(np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0))))
    out = [float(norm[k]), t_peak, ff, ff_len, width, jerk, float(pre.std()), float(post.std()),
           float(np.mean(np.abs(post - 1.0))), float(post.std() / (pre.std() + 0.01)), orient,
           float(g_pre[2] - g_post[2]), float(np.sum((post - 1.0) ** 2) / fs), float(np.sum(norm > 2.0))]
    v = np.asarray(out, dtype=np.float32)
    assert len(v) == len(FALL_FEATURES)
    return np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)


def window_samples(fs: float = IMU_FS) -> int:
    return int(FALL_WINDOW_S * fs)

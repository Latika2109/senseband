"""Waist-IMU window features for the activity / posture model (M1).

Input is accelerometer data in g in the BAND frame: x forward, y left, z up when standing (so gravity is
+z standing, mostly +/-x or y when lying). Dataset loaders rotate foreign sensors into this frame first.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import kurtosis, skew

from .common import EPS, IMU_FS

_BANDS = ((0.3, 1.0), (1.0, 3.0), (3.0, 6.0), (6.0, 25.0))

IMU_FEATURES = (
    ["mean_x", "mean_y", "mean_z", "mean_norm", "tilt_z_deg", "abs_mean_x", "abs_mean_y", "abs_mean_z"]
    + ["std_x", "std_y", "std_z", "std_norm", "mean_abs_norm_dev", "min_norm", "max_norm", "range_norm"]
    + ["p10_norm", "p90_norm", "kurt_norm", "skew_norm", "sma", "zcr_norm", "mean_jerk"]
    + ["band_0p3_1", "band_1_3", "band_3_6", "band_6_25", "dom_freq", "dom_peak_ratio", "spec_entropy"]
    + ["corr_xy", "corr_xz", "corr_yz", "z_dom_freq", "autocorr_peak", "autocorr_lag_s"]
)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    sa, sb = a.std(), b.std()
    return 0.0 if sa < 1e-6 or sb < 1e-6 else float(np.corrcoef(a, b)[0, 1])


def imu_window_features(acc_g: np.ndarray, fs: float = IMU_FS) -> np.ndarray:
    """Feature vector (len(IMU_FEATURES),) for one (n, 3) accelerometer window."""
    a = np.asarray(acc_g, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] != 3 or len(a) < 16:
        raise ValueError("expected an (n>=16, 3) accelerometer window")
    mean = a.mean(axis=0)
    norm = np.linalg.norm(a, axis=1)
    mnorm = float(np.linalg.norm(mean))
    tilt = float(np.degrees(np.arccos(np.clip(mean[2] / (mnorm + EPS), -1.0, 1.0))))
    dyn = a - mean                                   # gravity removed (per-window mean)
    dn = norm - norm.mean()
    std = a.std(axis=0)
    sma = float(np.mean(np.abs(dyn).sum(axis=1)))
    zcr = float(np.mean(np.diff(np.signbit(dn).astype(np.int8)) != 0))
    jerk = float(np.mean(np.linalg.norm(np.diff(a, axis=0), axis=1)) * fs)

    spec = np.abs(np.fft.rfft(dn * np.hanning(len(dn)))) ** 2
    freqs = np.fft.rfftfreq(len(dn), 1.0 / fs)
    tot = float(spec[freqs >= 0.3].sum()) + EPS
    bands = [float(spec[(freqs >= lo) & (freqs < hi)].sum() / tot) for lo, hi in _BANDS]
    rng = (freqs >= 0.5) & (freqs <= 5.0)
    if rng.any() and spec[rng].max() > 0:
        k = int(np.argmax(np.where(rng, spec, 0)))
        dom, peak_ratio = float(freqs[k]), float(spec[k] / tot)
    else:
        dom, peak_ratio = 0.0, 0.0
    p = spec[freqs >= 0.3] / tot
    entropy = float(-(p * np.log(p + EPS)).sum() / np.log(max(len(p), 2)))
    zspec = np.abs(np.fft.rfft((a[:, 2] - a[:, 2].mean()) * np.hanning(len(a)))) ** 2
    zr = (freqs >= 0.5) & (freqs <= 5.0)
    zdom = float(freqs[int(np.argmax(np.where(zr, zspec, 0)))]) if zr.any() and zspec[zr].max() > 0 else 0.0

    ac = np.correlate(dn, dn, mode="full")[len(dn) - 1:]
    ac = ac / (ac[0] + EPS)
    lo_lag, hi_lag = int(0.25 * fs), min(int(1.5 * fs), len(ac) - 1)
    if hi_lag > lo_lag:
        j = lo_lag + int(np.argmax(ac[lo_lag:hi_lag + 1]))
        acp, acl = float(ac[j]), j / fs
    else:
        acp, acl = 0.0, 0.0

    out = [*mean, mnorm, tilt, *np.abs(mean),
           *std, float(norm.std()), float(np.mean(np.abs(norm - 1.0))), float(norm.min()), float(norm.max()),
           float(np.ptp(norm)), float(np.percentile(norm, 10)), float(np.percentile(norm, 90)),
           float(kurtosis(norm)) if norm.std() > 1e-6 else 0.0, float(skew(norm)) if norm.std() > 1e-6 else 0.0,
           sma, zcr, jerk, *bands, dom, peak_ratio, entropy,
           _corr(dyn[:, 0], dyn[:, 1]), _corr(dyn[:, 0], dyn[:, 2]), _corr(dyn[:, 1], dyn[:, 2]), zdom, acp, acl]
    v = np.asarray(out, dtype=np.float32)
    assert len(v) == len(IMU_FEATURES), (len(v), len(IMU_FEATURES))
    return np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)

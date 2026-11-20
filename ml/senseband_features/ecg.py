"""ECG features: R peaks, RR-interval features for rhythm screening (M4) and signal-quality features (M3).

Single lead, any sampling rate >= 125 Hz (training resamples PhysioNet data to 250 Hz, like the edge ADC).
RR features follow the CinC-2017 literature: variability, entropy, Poincare, turning points.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt, welch
from scipy.stats import kurtosis, skew

from .common import ECG_FS, EPS

RR_FEATURES = [
    "n_beats", "mean_rr", "hr_bpm", "sdnn", "rmssd", "pnn50", "pnn20", "cv_rr", "range_rr", "iqr_rr",
    "mad_diff", "sd1", "sd2", "sd_ratio", "sampen", "cosen", "turning_point_ratio", "shannon_entropy",
    "frac_irregular", "frac_short", "frac_long", "max_over_median",
]

ECG_QUALITY_FEATURES = [
    "std", "ptp", "kurtosis", "skew", "flat_frac", "clip_frac", "qrs_band_ratio", "baseline_ratio",
    "hf_ratio", "n_peaks", "rr_cv", "template_corr", "template_corr_min", "peak_amp_ratio", "diff_std_ratio",
]


# ------------------------------------------------------------------ R peaks
def bandpass(x: np.ndarray, fs: float, lo: float = 8.0, hi: float = 25.0) -> np.ndarray:
    sos = butter(2, [lo, min(hi, fs / 2 - 1)], btype="bandpass", fs=fs, output="sos")
    return sosfiltfilt(sos, x)


def r_peaks(x: np.ndarray, fs: float = ECG_FS) -> np.ndarray:
    """R-peak sample indices: 8-25 Hz band-pass, slope+amplitude envelope, adaptive threshold, refractory 0.25 s.

    Band and envelope were chosen on CinC-2017 by subject-wise AF F1 (see metrics.json history in CHANGELOG)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < fs * 2 or not np.isfinite(x).all() or np.ptp(x) < 1e-6:
        return np.array([], dtype=int)
    y = bandpass(x - np.median(x), fs)
    env = np.convolve(np.abs(np.diff(y, prepend=y[0])) + np.abs(y), np.ones(int(0.08 * fs)) / int(0.08 * fs), "same")
    thr = 0.3 * np.percentile(env, 99)
    cand, _ = find_peaks(env, height=thr, distance=int(0.25 * fs))
    if len(cand) == 0:
        return cand
    half = int(0.06 * fs)
    ref = np.array([max(0, c - half) + int(np.argmax(np.abs(y[max(0, c - half):c + half + 1]))) for c in cand])
    return np.unique(ref)


def rr_intervals(peaks: np.ndarray, fs: float = ECG_FS) -> np.ndarray:
    """Seconds between beats; physiologically impossible gaps (< 0.2 s or > 3 s) are dropped."""
    rr = np.diff(np.asarray(peaks, dtype=np.float64)) / fs
    return rr[(rr >= 0.2) & (rr <= 3.0)]


# ------------------------------------------------------------------ RR features
def _sampen(x: np.ndarray, m: int = 2, r_frac: float = 0.2) -> float:
    n = len(x)
    if n < m + 3:
        return 0.0
    r = r_frac * x.std()
    if r < 1e-9:
        return 0.0

    def phi(mm: int) -> float:
        t = np.lib.stride_tricks.sliding_window_view(x, mm)[: n - m]
        d = np.max(np.abs(t[:, None, :] - t[None, :, :]), axis=2)
        return float((np.sum(d <= r) - len(t)) / 2.0)

    a, b = phi(m + 1), phi(m)
    return float(-np.log(a / b)) if a > 0 and b > 0 else float(np.log(max(n - m, 2)))


def rr_features(rr: np.ndarray) -> np.ndarray:
    """Feature vector (len(RR_FEATURES),) from RR intervals in seconds (any count; short input is safe)."""
    r = np.asarray(rr, dtype=np.float64)
    n = len(r)
    if n < 4:
        v = np.zeros(len(RR_FEATURES), dtype=np.float32)
        v[0] = n + 1
        return v
    if n > 120:
        r = r[:120]
        n = 120
    mean = r.mean()
    d = np.diff(r)
    sd1 = float(np.std(d) / np.sqrt(2))
    sd2 = float(np.sqrt(max(2 * r.std() ** 2 - sd1 ** 2, 0.0)))
    se = _sampen(r)
    cosen = se + float(np.log(2 * 0.2 * r.std() + EPS)) - float(np.log(mean))
    tp = np.sum((r[1:-1] > r[:-2]) & (r[1:-1] > r[2:])) + np.sum((r[1:-1] < r[:-2]) & (r[1:-1] < r[2:]))
    tpr = float(tp / (2.0 / 3.0 * (n - 2))) if n > 2 else 0.0
    hist, _ = np.histogram(r, bins=np.linspace(0.3, 2.0, 18))
    p = hist[hist > 0] / max(hist.sum(), 1)
    out = [n + 1, mean, 60.0 / mean, r.std(), float(np.sqrt(np.mean(d ** 2))), float(np.mean(np.abs(d) > 0.05)),
           float(np.mean(np.abs(d) > 0.02)), r.std() / mean, float(np.ptp(r)),
           float(np.subtract(*np.percentile(r, [75, 25]))), float(np.median(np.abs(d))), sd1, sd2,
           sd1 / (sd2 + EPS), se, cosen, tpr, float(-(p * np.log(p)).sum()), float(np.mean(np.abs(d) > 0.1 * mean)),
           float(np.mean(r < 0.8 * mean)), float(np.mean(r > 1.2 * mean)), float(r.max() / np.median(r))]
    v = np.asarray(out, dtype=np.float32)
    assert len(v) == len(RR_FEATURES)
    return np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)


# ------------------------------------------------------------------ signal quality
def ecg_quality_features(x: np.ndarray, fs: float = ECG_FS) -> np.ndarray:
    """Quality features for a ~5 s ECG window (mV or any linear scale: features are scale-free)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < fs * 2:
        raise ValueError("need at least 2 s of ECG")
    x = np.nan_to_num(x)
    xc = x - np.median(x)
    std, ptp = float(xc.std()), float(np.ptp(xc))
    d = np.diff(xc)
    flat = float(np.mean(np.abs(d) < 1e-3 * (ptp + EPS)))
    clip = float(np.mean(np.abs(xc) >= 0.98 * (np.max(np.abs(xc)) + EPS))) if ptp > 0 else 1.0
    f, pxx = welch(xc, fs=fs, nperseg=min(len(xc), int(fs * 2)))
    tot = float(pxx[(f >= 0.5) & (f <= 40)].sum()) + EPS
    qrs = float(pxx[(f >= 5) & (f <= 15)].sum() / tot)
    base = float(pxx[f < 0.5].sum() / (pxx[f <= 40].sum() + EPS))
    hf = float(pxx[(f > 40)].sum() / (pxx.sum() + EPS))
    peaks = r_peaks(x, fs)
    rr = rr_intervals(peaks, fs)
    cv = float(rr.std() / rr.mean()) if len(rr) >= 2 else 1.0
    tcorr = tmin = 0.0
    amp_ratio = 0.0
    if len(peaks) >= 3:
        half = int(0.25 * fs)
        beats = np.asarray([xc[p - half:p + half] for p in peaks if p - half >= 0 and p + half <= len(xc)])
        if len(beats) >= 3:
            tpl = np.median(beats, axis=0)
            cs = [np.corrcoef(b, tpl)[0, 1] for b in beats if b.std() > 1e-9 and tpl.std() > 1e-9]
            if cs:
                tcorr, tmin = float(np.mean(cs)), float(np.min(cs))
        amps = np.abs(xc[peaks])
        amp_ratio = float(amps.max() / (np.median(amps) + EPS))
    diff_ratio = float(np.std(d) / (std + EPS))
    out = [std, ptp, float(kurtosis(xc)) if std > 1e-9 else 0.0, float(skew(xc)) if std > 1e-9 else 0.0, flat, clip,
           qrs, base, hf, float(len(peaks)), cv, tcorr, tmin, amp_ratio, diff_ratio]
    v = np.asarray(out, dtype=np.float32)
    assert len(v) == len(ECG_QUALITY_FEATURES)
    return np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)

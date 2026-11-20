"""Small helpers to sanity-check simulated streams (also used by the CLI and tests)."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt


def ecg_r_peaks(mv: np.ndarray, fs: int) -> np.ndarray:
    """Indices of R peaks (band-pass 5-20 Hz, adaptive threshold)."""
    if len(mv) < fs * 2:
        return np.array([], dtype=int)
    sos = butter(2, [5, 20], btype="band", fs=fs, output="sos")
    x = np.abs(sosfiltfilt(sos, mv))
    thr = 0.5 * np.percentile(x, 99.5)
    if thr < 0.05:                                    # flat line: nothing to find
        return np.array([], dtype=int)
    return find_peaks(x, height=thr, distance=int(0.25 * fs))[0]


def hr_from_ecg(mv: np.ndarray, fs: int) -> float:
    """Mean heart rate (bpm) from an ECG window; nan if fewer than 3 beats."""
    p = ecg_r_peaks(mv, fs)
    if len(p) < 3:
        return float("nan")
    return float(60.0 / np.mean(np.diff(p) / fs))


def rr_cv(mv: np.ndarray, fs: int) -> float:
    """Coefficient of variation of RR intervals (irregularity)."""
    rr = np.diff(ecg_r_peaks(mv, fs)) / fs
    return float(np.std(rr) / np.mean(rr)) if len(rr) > 2 else float("nan")


def spo2_from_ppg(red: np.ndarray, ir: np.ndarray, fs: int, a: float = 110.0,
                  b: float = 25.0) -> float:
    """Textbook SpO2 = a - b*R with R = (AC/DC)red / (AC/DC)ir, AC = band-passed RMS."""
    sos = butter(2, [0.5, 5.0], btype="band", fs=fs, output="sos")
    ac_r, ac_i = np.std(sosfiltfilt(sos, red)), np.std(sosfiltfilt(sos, ir))
    if ac_i <= 0:
        return float("nan")
    return float(a - b * (ac_r / np.mean(red)) / (ac_i / np.mean(ir)))

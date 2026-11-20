"""ECG signal-quality gate + heart-rate / RR analysis (rule-based fallback for models M3/M4).

Quality states: collecting | lead_off | noisy | flat | good (the engine adds "paused" while the ECG is duty-cycled off). Only "good" may feed vital alerts.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, find_peaks, sosfilt, sosfilt_zi

from ..config import Quality


@dataclass
class EcgResult:
    quality: str = "collecting"
    hr: float | None = None
    rr_cv: float | None = None            # RR variability over the last 30 s (None if < 20 beats)
    beats_30s: int = 0
    last_beat_age_s: float | None = None
    flat_s: float = 0.0                   # how long the contact-OK signal has been flat
    new_beats: list[tuple[float, float]] = field(default_factory=list)   # (t, rr_s)


class EcgAnalyzer:
    BUF_S = 10.0
    NOISE_HOLD_S = 4.0

    def __init__(self, fs: int, q: Quality,
                 quality_fn: Callable[[np.ndarray], dict[str, float] | None] | None = None, min_prob: float = 0.75):
        self.fs, self.q = fs, q
        self.quality_fn, self.quality_min_prob = quality_fn, min_prob
        self.bp = butter(2, [0.5, 40], btype="bandpass", fs=fs, output="sos")
        self.hp = butter(2, 30, btype="highpass", fs=fs, output="sos")
        n = int(fs * self.BUF_S)
        self.buf: deque[float] = deque(maxlen=n)      # band-passed
        self.hf: deque[float] = deque(maxlen=n)       # high-passed (noise estimate)
        self.zi_bp = self.zi_hp = None
        self.t_end = 0.0
        self.lead_off_now = False
        self.contact_since: float | None = None
        self.qrs_amp: float | None = None
        self.beats: deque[float] = deque(maxlen=200)  # absolute R-peak times (s)
        self.rr: deque[tuple[float, float]] = deque(maxlen=200)
        self.flat_since: float | None = None
        self._noisy_until = -1.0
        self.raw_ring: deque[float] = deque(maxlen=int(fs * 40))

    def reset(self) -> None:
        self.buf.clear()
        self.hf.clear()
        self.zi_bp = self.zi_hp = None
        self.qrs_amp = None
        self.flat_since = None

    def add_chunk(self, t_end: float, mv: np.ndarray, lead_off: np.ndarray) -> None:
        self.t_end = t_end
        mv = np.asarray(mv, dtype=float)
        if len(mv) == 0:                       # sensor missing this frame: degrade, don't crash
            return
        self.raw_ring.extend(mv.tolist())
        if lead_off.any():
            if not self.lead_off_now:
                self.reset()
            self.lead_off_now, self.contact_since = True, None
            return
        if self.lead_off_now or self.contact_since is None:
            self.reset()
            self.contact_since = t_end - len(mv) / self.fs
        self.lead_off_now = False
        if self.zi_bp is None:
            self.zi_bp = sosfilt_zi(self.bp) * mv[0]
            self.zi_hp = sosfilt_zi(self.hp) * mv[0]
        y, self.zi_bp = sosfilt(self.bp, mv, zi=self.zi_bp)
        h, self.zi_hp = sosfilt(self.hp, mv, zi=self.zi_hp)
        self.buf.extend(y.tolist())
        self.hf.extend(h.tolist())

    def recent_rr(self, now: float, seconds: float = 30.0) -> np.ndarray:
        return np.array([r for t, r in self.rr if now - t <= seconds], dtype=np.float64)

    def strip(self, seconds: float) -> np.ndarray:
        n = int(seconds * self.fs)
        return np.asarray(list(self.raw_ring)[-n:], dtype=np.float32)

    # ---- analysis (call about once a second) ------------------------------------------------
    def analyze(self, now: float) -> EcgResult:
        res = EcgResult()
        if self.lead_off_now:
            res.quality = "lead_off"
            return res
        fs, q = self.fs, self.q
        if self.contact_since is None:
            return res
        # 1) noise gate on the last 3 s (also while still collecting, so noise is flagged fast)
        if len(self.hf) >= fs * 1.5:
            hf = np.asarray(self.hf)[-int(fs * 3):]
            blk = fs // 2
            blocks = hf[: len(hf) // blk * blk].reshape(-1, blk)
            hf_rms = float(np.median(np.sqrt((blocks ** 2).mean(axis=1))))
            if hf_rms > q.ecg_noise_hf_mv:
                self._noisy_until = now + self.NOISE_HOLD_S
                self._flush()                      # noisy samples must never reach HR / RR
        if now < self._noisy_until:
            res.quality = "noisy"
            return res
        if len(self.buf) < fs * 4:
            return res                             # collecting
        if self.quality_fn is not None and len(self.raw_ring) >= int(5 * fs):
            probs = self.quality_fn(self.strip(5.0))        # model M3 on the last 5 s (rules above still apply)
            if probs:
                label, p = max(probs.items(), key=lambda kv: kv[1])
                if label == "noisy" and p >= self.quality_min_prob:
                    self._noisy_until = now + self.NOISE_HOLD_S
                    self._flush()
                    res.quality = "noisy"
                    return res
        x = np.asarray(self.buf)[int(fs * 0.5):]  # skip filter settling
        ptp2 = float(np.ptp(x[-int(fs * 2):]))
        peaks = self._peaks(x)
        t0 = self.t_end - len(x) / fs
        self._update_beats(t0 + peaks / fs, res)
        res.last_beat_age_s = (now - self.beats[-1]) if self.beats else None
        if ptp2 < q.ecg_flat_mv and (res.last_beat_age_s is None or res.last_beat_age_s > 2.0):
            res.quality = "flat"
            self.flat_since = self.flat_since if self.flat_since is not None else now
            res.flat_s = now - self.flat_since
            return res
        self.flat_since = None
        res.quality = "good"
        recent = [(t, r) for t, r in self.rr if now - t <= 30.0]
        res.beats_30s = len(recent)
        if len(peaks) >= 4:
            rr = np.diff(peaks) / fs
            rr = rr[(rr > 0.3) & (rr < 2.0)]
            if len(rr) >= 3:
                res.hr = round(60.0 / float(np.median(rr)), 1)
        if len(recent) >= 20:
            r = np.array([v for _, v in recent])
            res.rr_cv = float(np.std(r) / np.mean(r))
        return res

    def _flush(self) -> None:
        self.buf.clear()
        self.hf.clear()
        self.beats.clear()
        self.rr.clear()
        self.zi_bp = self.zi_hp = None
        self.flat_since = None

    def _peaks(self, x: np.ndarray) -> np.ndarray:
        q = self.q
        span = float(np.percentile(x, 99.5) - np.percentile(x, 0.5))
        if span < q.ecg_min_qrs_mv:
            return np.array([], dtype=int)
        lo, hi = np.percentile(x, [0.5, 99.5])
        if abs(lo) > abs(hi) * 1.3:                       # inverted lead
            x = -x
        thr = max(q.ecg_min_qrs_mv, q.ecg_qrs_frac * (self.qrs_amp or 0.0))
        peaks, props = find_peaks(x, distance=int(0.3 * self.fs), prominence=thr)
        if len(peaks):
            amp = float(np.median(props["prominences"]))
            self.qrs_amp = amp if self.qrs_amp is None else 0.9 * self.qrs_amp + 0.1 * amp
        return peaks

    def _update_beats(self, times: np.ndarray, res: EcgResult) -> None:
        last = self.beats[-1] if self.beats else -1e9
        for t in times:
            if t > last + 0.3:
                if self.beats and 0.3 < t - self.beats[-1] < 2.0:
                    self.rr.append((float(t), float(t - self.beats[-1])))
                    res.new_beats.append((float(t), float(t - self.beats[-1])))
                self.beats.append(float(t))
                last = float(t)

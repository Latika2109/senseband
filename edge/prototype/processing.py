"""Signal processing and the 'intelligence' layer:
ECG heart rate + rhythm, orientation, joint angle / ROM, fall detection,
personal baseline anomaly detection, exercise suggestions."""
import math
import threading
from collections import deque

import numpy as np
from scipy.signal import butter, find_peaks, sosfilt, sosfilt_zi


# ---------------------------------------------------------------- orientation
class Orientation:
    """Complementary filter: gyro for fast motion, accelerometer to stop drift."""

    def __init__(self, alpha=0.98):
        self.alpha, self.pitch, self.roll = alpha, None, 0.0

    def update(self, ax, ay, az, gx, gy, dt):
        acc_pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        acc_roll = math.degrees(math.atan2(ay, az))
        if self.pitch is None:
            self.pitch, self.roll = acc_pitch, acc_roll
        else:
            a = self.alpha
            self.pitch = a * (self.pitch + gy * dt) + (1 - a) * acc_pitch
            self.roll = a * (self.roll + gx * dt) + (1 - a) * acc_roll
        return self.pitch, self.roll


# ---------------------------------------------------------------- ECG
class ECGAnalyzer:
    def __init__(self, fs, window_s=8):
        self.fs = fs
        self.sos = butter(2, [0.5, 40], btype="bandpass", fs=fs, output="sos")
        self.zi = None
        self.buf = deque(maxlen=int(fs * window_s))
        self.lock = threading.Lock()

    def clear(self):
        with self.lock:
            self.buf.clear()
            self.zi = None

    def add_chunk(self, raw):
        raw = np.asarray(raw, dtype=float)
        if self.zi is None:
            self.zi = sosfilt_zi(self.sos) * raw[0]
        y, self.zi = sosfilt(self.sos, raw, zi=self.zi)
        with self.lock:
            self.buf.extend(y.tolist())

    def wave(self, seconds=3, step=2):
        with self.lock:
            x = list(self.buf)[-int(self.fs * seconds):]
        return [round(v, 4) for v in x[::step]]

    def analyze(self):
        with self.lock:
            x = np.asarray(self.buf)
        if len(x) < self.fs * 4:
            return {"hr": None, "irregularity": None, "status": "collecting"}
        x = x[int(self.fs * 0.5):]                       # skip filter warm-up edge
        lo, hi = np.percentile(x, [0.5, 99.5])
        span = hi - lo
        if span < 0.02:
            return {"hr": None, "irregularity": None, "status": "flat signal"}
        if abs(lo) > abs(hi) * 1.3:                      # R-wave pointing down
            x = -x
        peaks, _ = find_peaks(x, distance=int(0.3 * self.fs), prominence=0.45 * span)
        rr = np.diff(peaks) / self.fs
        rr = rr[(rr > 0.3) & (rr < 2.0)]
        if len(rr) < 3:
            return {"hr": None, "irregularity": None, "status": "no clear beats"}
        hr = 60.0 / float(np.median(rr))
        irr = float(np.std(rr) / np.mean(rr))
        return {"hr": round(hr, 1), "irregularity": round(irr, 3), "status": "ok"}


# ---------------------------------------------------------------- ROM
class RomTracker:
    """Joint angle = (upper segment pitch - lower segment pitch) - calibration offset.
    Main band above the joint, small band below it, both with the same side facing out."""

    def __init__(self):
        self.offset = 0.0
        self.active = False
        self.joint = "elbow"
        self.reset()

    def reset(self):
        self.min = self.max = None
        self.reps, self.peak_speed = 0, 0.0
        self._last = None
        self._high = False

    def calibrate(self, upper, lower):
        self.offset = upper - lower          # limb held straight = 0 deg

    def angle(self, upper, lower):
        return abs((upper - lower) - self.offset)

    def update(self, ang, t):
        if not self.active:
            return
        self.min = ang if self.min is None else min(self.min, ang)
        self.max = ang if self.max is None else max(self.max, ang)
        if self._last:
            la, lt = self._last
            if t > lt:
                self.peak_speed = max(self.peak_speed, abs(ang - la) / (t - lt))
        self._last = (ang, t)
        # repetition = bend past 30 deg then come back under 10 deg
        if not self._high and ang > 30:
            self._high = True
        elif self._high and ang < 10:
            self._high = False
            self.reps += 1

    def summary(self):
        if self.max is None:
            return None
        return {"joint": self.joint, "min_angle": round(self.min, 1),
                "max_angle": round(self.max, 1), "rom": round(self.max - self.min, 1),
                "reps": self.reps, "peak_speed": round(self.peak_speed, 1)}


# ---------------------------------------------------------------- falls
class FallDetector:
    """Free-fall -> impact -> lying still = fall."""

    def __init__(self, cfg):
        self.c = cfg
        self.ff_t = self.impact_t = None
        self.moving = self.count = 0

    def update(self, amag, t):
        c = self.c
        if amag < c.FALL_FREEFALL_G:
            self.ff_t = t
        recent_ff = self.ff_t is not None and t - self.ff_t < 1.0
        if self.impact_t is None:
            if (amag > c.FALL_IMPACT_G and recent_ff) or amag > 3.5:
                self.impact_t, self.moving, self.count = t, 0, 0
            return None
        dt = t - self.impact_t
        if dt > 2.0:
            self.count += 1
            if abs(amag - 1.0) > 0.15:
                self.moving += 1
        if dt > c.FALL_STILL_S:
            still = self.count > 0 and self.moving / self.count < 0.1
            self.impact_t = self.ff_t = None
            return "fall" if still else "impact"
        return None


# ---------------------------------------------------------------- personal baseline
class Baseline:
    """Learns what is normal for THIS person and flags big deviations (z-score)."""

    def __init__(self, n=720):
        self.vals = deque(maxlen=n)

    def check(self, v):
        if v is None:
            return None
        z = None
        if len(self.vals) >= 60:
            m, s = float(np.mean(self.vals)), float(np.std(self.vals))
            if s > 0.5:
                z = (v - m) / s
        self.vals.append(v)
        return z


# ---------------------------------------------------------------- exercises
NORMAL_ROM = {"elbow": 145, "knee": 135, "wrist": 150, "shoulder": 180, "ankle": 70, "other": 120}

ROM_STAGES = [
    (40, "Early mobility", [
        "Gentle assisted {j} bending/straightening – 10 reps x 3 sets, pain-free range only",
        "Hold the end position 10–15 s, relax, repeat 5 times",
        "Elevate and ice after sessions if swelling increases"]),
    (75, "Active range of motion", [
        "Active {j} bend/straighten through full available range – 15 reps x 3",
        "Slow and controlled: 3 s up, 3 s down",
        "Light daily tasks (lifting a cup, towel slides on a table)"]),
    (101, "Strengthening", [
        "Resistance-band {j} exercises – 12 reps x 3",
        "Light weights (0.5–1 kg), increase gradually",
        "End-range holds to keep the last few degrees"]),
]

GRIP_STAGES = [
    (50, ["Soft sponge / putty squeezes – 10 reps x 3", "Finger spreads against a rubber band"]),
    (80, ["Medium putty or ball squeeze with 5 s hold – 10 reps x 3", "Towel wringing"]),
    (999, ["Firm grip trainer – 12 reps x 3", "Light farmer carries"]),
]


def suggest_exercises(rom_summary, prev_rom=None, grip_kg=None, grip_ref_kg=None):
    out = {"note": "Prototype suggestions – confirm with your physiotherapist before starting."}
    if rom_summary:
        j = rom_summary["joint"]
        pct = min(100.0, rom_summary["rom"] / NORMAL_ROM.get(j, 120) * 100)
        for limit, name, items in ROM_STAGES:
            if pct < limit:
                out.update(rom_pct=round(pct), stage=name,
                           exercises=[e.format(j=j) for e in items])
                break
        if prev_rom is not None:
            out["change_since_last"] = round(rom_summary["rom"] - prev_rom, 1)
    if grip_kg:
        ref = grip_ref_kg or 30.0            # other hand if known, else rough adult average
        gpct = grip_kg / ref * 100
        out["grip_pct"] = round(gpct)
        out["grip_exercises"] = next(items for limit, items in GRIP_STAGES if gpct < limit)
    return out

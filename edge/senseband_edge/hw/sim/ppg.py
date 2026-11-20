"""Synthetic MAX30102 red/IR PPG. SpO2 is encoded in the red/IR ratio-of-ratios."""
from __future__ import annotations

from typing import Any

import numpy as np

from ..frames import PpgChunk
from .heart import BeatSchedule
from .noise import multisine
from .scenario import Timeline


class PpgGenerator:
    def __init__(self, tl: Timeline, cfg: dict[str, Any], beats: BeatSchedule, seed: int):
        self.tl, self.c, self.beats = tl, cfg["ppg"], beats
        self.ptt = float(cfg["heart"]["ptt_s"])
        self.fs = int(cfg["rates_hz"]["ppg"])
        self.rng = np.random.default_rng([seed, 2])
        r = np.random.default_rng([seed, 12])
        self._art_f = r.uniform(0.8, 4.0, 3)
        self._art_p = r.uniform(0, 2 * np.pi, 3)

    @staticmethod
    def _pulse(tau: np.ndarray) -> np.ndarray:
        """Systolic peak + dicrotic wave, peak ~1."""
        return (np.exp(-0.5 * ((tau - 0.15) / 0.06) ** 2)
                + 0.30 * np.exp(-0.5 * ((tau - 0.38) / 0.07) ** 2))

    def chunk(self, i0: int, i1: int) -> PpgChunk:
        c, fs = self.c, self.fs
        t = np.arange(i0, i1) / fs
        n = len(t)
        pulse = np.zeros(n)
        for b in self.beats.between(t[0] - self.ptt - 1.0, t[-1] - self.ptt + 0.2):
            amp = float(np.clip(b.rr / 0.8, 0.5, 1.1)) if b.af else 1.0
            pulse += amp * self._pulse(t - (b.t + self.ptt))
        spo2 = np.minimum(self.tl.spo2(t), 100.0)
        r_ratio = (c["spo2_a"] - spo2) / c["spo2_b"]       # ratio of ratios
        pi_ir = c["perfusion_index"]
        motion = self.tl.num("motion", t)
        art = multisine(self._art_f, self._art_p, t)
        slow = 1 + 0.01 * np.sin(2 * np.pi * 0.1 * t)
        mot = motion * c["motion_frac"] * art
        ir = c["ir_dc"] * slow * (1 - pi_ir * pulse + mot)
        red = c["red_dc"] * slow * (1 - pi_ir * r_ratio * pulse + mot * 0.9)
        ir += self.rng.normal(0, c["noise_frac"] * c["ir_dc"], n)
        red += self.rng.normal(0, c["noise_frac"] * c["red_dc"], n)
        off = self.tl.flag("finger_off", t)
        if off.any():
            ir = np.where(off, c["finger_off_ir"] + self.rng.normal(0, 40, n), ir)
            red = np.where(off, c["finger_off_red"] + self.rng.normal(0, 40, n), red)
        ir, red = np.clip(ir, 0, 262143), np.clip(red, 0, 262143)
        return PpgChunk(fs, i0 / fs, red.astype(np.int32), ir.astype(np.int32),
                        ir > c["finger_threshold"])

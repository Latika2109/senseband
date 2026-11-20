"""Synthetic single-lead ECG (AD8232-like): normal, AF, noisy, lead-off, asystole."""
from __future__ import annotations

from typing import Any

import numpy as np

from ..frames import EcgChunk
from .heart import BeatSchedule
from .noise import multisine
from .scenario import Timeline


class EcgGenerator:
    def __init__(self, tl: Timeline, cfg: dict[str, Any], beats: BeatSchedule, seed: int):
        self.tl, self.c, self.beats = tl, cfg["ecg"], beats
        self.fs = int(cfg["rates_hz"]["ecg"])
        self.rng = np.random.default_rng([seed, 1])
        r = np.random.default_rng([seed, 11])          # fixed sinusoid set (chunk-continuous)
        self._art_f = r.uniform(1.0, 6.0, 4)
        self._art_p = r.uniform(0, 2 * np.pi, 4)
        self._wander_p = r.uniform(0, 2 * np.pi, 2)

    def chunk(self, i0: int, i1: int) -> EcgChunk:
        c, fs = self.c, self.fs
        t = np.arange(i0, i1) / fs
        n = len(t)
        sig = np.zeros(n)
        for b in self.beats.between(t[0] - 0.8, t[-1] + 0.8):
            scale = float(np.clip(np.sqrt(max(b.rr, 0.3) / 0.9), 0.6, 1.2))
            tau = t - b.t
            sig += c["r_amp_mv"] * (1 + 0.05 * np.sin(2 * np.pi * 0.25 * b.t)) \
                * np.exp(-0.5 * (tau / 0.011) ** 2)
            for name, (mu, sd, a) in c["waves"].items():
                if name == "P" and b.af:
                    continue                           # no organised atrial activity in AF
                s = scale if name in ("P", "T") else 1.0
                sig += a * np.exp(-0.5 * ((tau - mu * s) / (sd * s)) ** 2)
        af = np.array([self.tl.step("rhythm", x) == "af" for x in t[:: max(n // 2, 1)]])
        if af.any():
            sig += c["af_fib_mv"] * (np.sin(2 * np.pi * 5.3 * t) * np.sin(2 * np.pi * 0.7 * t + 1))
        motion = self.tl.num("motion", t)
        noise_lvl = self.tl.num("ecg_noise", t)
        art = multisine(self._art_f, self._art_p, t)
        sig += c["baseline_wander_mv"] * (
            np.sin(2 * np.pi * 0.3 * t + self._wander_p[0])
            + 0.5 * np.sin(2 * np.pi * 0.05 * t + self._wander_p[1]))
        sig += c["powerline_mv"] * np.sin(2 * np.pi * c["powerline_hz"] * t)
        sig += self.rng.normal(0.0, c["white_mv"], n)
        sig += motion * c["motion_mv"] * art
        sig += noise_lvl * c["noise_mv"] * (self.rng.normal(0.0, 0.5, n) + 0.8 * art)
        lead_off = self.tl.flag("lead_off", t)
        if lead_off.any():
            hum = c["lead_off_hum_mv"] * np.sin(2 * np.pi * c["powerline_hz"] * t)
            floating = hum + 0.05 * np.sin(2 * np.pi * 0.2 * t) + self.rng.normal(0, 0.01, n)
            sig = np.where(lead_off, floating, sig)
        return EcgChunk(fs, i0 / fs, sig, lead_off)

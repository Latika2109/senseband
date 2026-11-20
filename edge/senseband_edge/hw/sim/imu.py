"""Synthetic MPU6050s: waist, reference limb (main band) and moving limb (small band)."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation as R
from scipy.spatial.transform import Slerp

from ..frames import ImuChunk
from .scenario import Timeline

UP = np.array([0.0, 0.0, 1.0])


def _euler(v: list[float]) -> R:
    return R.from_euler("xyz", v, degrees=True)


class ImuGenerator:
    NAMES = ("waist", "limb_ref", "limb_moving")

    def __init__(self, tl: Timeline, cfg: dict[str, Any], seed: int):
        self.tl, self.c = tl, cfg["imu"]
        self.fs = int(cfg["rates_hz"]["imu"])
        self.rng = np.random.default_rng([seed, 3])
        self._phase = 0.0                      # gait phase, carried across chunks
        self._slerp = {n: self._build(n) for n in self.NAMES}
        self._lo, self._hi = self._slerp["waist"].times[[0, -1]]

    # ---- orientation ------------------------------------------------------------------
    def _static(self, name: str, seg) -> R:
        p = seg.p
        post = _euler(self.c["orientations"][p["posture"]])
        if p.get("joint") or p.get("limbs") == "rehab" or p["activity"].startswith("exercise_"):
            if name == "waist":
                return post
            return _euler(p["ref_euler_deg"])          # both limb IMUs start aligned
        if name == "waist":
            return post
        return post * _euler(self.c["mounts_euler_deg"][name])

    def _build(self, name: str) -> Slerp:
        times, rots = [], []

        def add(t: float, r: R) -> None:
            if times and t <= times[-1]:
                t = times[-1] + 1e-6
            times.append(t)
            rots.append(r)

        prev: R | None = None
        for seg in self.tl.segments:
            q = self._static(name, seg)
            if prev is not None:
                add(seg.start, prev)
                add(seg.start + seg.trans_s, q)
            else:
                add(seg.start, q)
            prev = q
        add(self.tl.duration_s + 10.0, prev)
        return Slerp(times, R.concatenate(rots))

    def _orient(self, name: str, t: np.ndarray, extra_y_deg: np.ndarray) -> R:
        base = self._slerp[name](np.clip(t, self._lo, self._hi))
        return base * R.from_rotvec(np.outer(np.deg2rad(extra_y_deg), [0.0, 1.0, 0.0]))

    # ---- chunk ------------------------------------------------------------------------
    def chunks(self, i0: int, i1: int) -> dict[str, ImuChunk]:
        fs, c = self.fs, self.c
        t = np.arange(i0, i1) / fs
        n = len(t)
        dt = 1.0 / fs
        cad = self.tl.num("cadence_hz", t)
        phase = self._phase + np.cumsum(2 * np.pi * cad * dt)
        self._phase = float(phase[-1])
        vert, lat = self.tl.num("vert_g", t), self.tl.num("lat_g", t)
        gyro_amp = self.tl.num("gyro_dps", t)
        breathing = self.tl.num("breathing_g", t)
        swing = self.tl.num("arm_swing_deg", t) * np.sin(phase / 2)
        joint = self.tl.joint_angle(t)
        joint_next = self.tl.joint_angle(t + dt)
        out: dict[str, ImuChunk] = {}
        for name in self.NAMES:
            if name == "waist":
                extra, extra_n = np.zeros(n), np.zeros(n)
            elif name == "limb_ref":
                extra, extra_n = 0.6 * swing, 0.6 * self.tl.num("arm_swing_deg", t + dt) \
                    * np.sin((phase + 2 * np.pi * cad * dt) / 2)
            else:
                extra = swing + joint
                extra_n = self.tl.num("arm_swing_deg", t + dt) \
                    * np.sin((phase + 2 * np.pi * cad * dt) / 2) + joint_next
            r0 = self._orient(name, t, extra)
            r1 = self._orient(name, t + dt, extra_n)
            gyro = (r0.inv() * r1).as_rotvec(degrees=True) * fs
            acc = r0.inv().apply(UP)
            if name == "waist":
                lin = np.stack([0.4 * vert * np.sin(phase + 0.8),
                                lat * np.sin(phase / 2),
                                vert * (np.sin(phase) + 0.35 * np.sin(2 * phase))
                                + breathing * np.sin(2 * np.pi * 0.25 * t)], axis=1)
                acc = acc + lin
                gyro += np.stack([0.5 * gyro_amp * np.sin(phase / 2 + 1.0),
                                  0.3 * gyro_amp * np.sin(phase),
                                  gyro_amp * np.sin(phase / 2)], axis=1)
                acc = self._events(acc, t)
            acc += self.rng.normal(0, c["acc_noise_g"], (n, 3)) \
                + c["tremor_g"] * np.sin(2 * np.pi * 9.0 * t)[:, None]
            gyro += self.rng.normal(0, c["gyro_noise_dps"], (n, 3))
            jitter = R.from_rotvec(self.rng.normal(0, np.deg2rad(c["quat_noise_deg"]), (n, 3)))
            q = (r0 * jitter).as_quat()                     # x, y, z, w
            q = q * np.where(q[:, 3:4] < 0, -1.0, 1.0)      # keep w >= 0
            quat = np.column_stack([q[:, 3], q[:, 0], q[:, 1], q[:, 2]])
            out[name] = ImuChunk(fs, i0 / fs, acc, gyro, quat)
        return out

    def _events(self, acc: np.ndarray, t: np.ndarray) -> np.ndarray:
        for te, kind in self.tl.events:
            e = self.c[kind]
            if t[-1] < te - 0.1 or t[0] > te + e["freefall_s"] + 1.0:
                continue
            ff = (t >= te) & (t < te + e["freefall_s"])
            acc = np.where(ff[:, None], acc * e["freefall_g"], acc)
            ti = te + e["freefall_s"]
            d = np.array(e["direction"], dtype=float)
            d /= np.linalg.norm(d)
            pulse = e["impact_g"] * np.exp(-0.5 * ((t - ti) / e["impact_sigma_s"]) ** 2)
            ring = np.where(t >= ti, np.sin(2 * np.pi * 14 * (t - ti)) * np.exp(-(t - ti) / 0.15),
                            0.0)
            acc = acc + (pulse + 0.4 * e["impact_g"] * 0.25 * ring)[:, None] * d
        return acc

"""Emulates the small band's BLE text protocol (firmware/small_band/small_band.ino), so the
prototype parsers and the future BLE link can be tested without an ESP32.

    I,<ms>,<pitch>,<roll>                    25 Hz
    V,<hr>,<hrValid>,<spo2>,<spo2Valid>,<finger>   1 Hz   (-999 when invalid, like Maxim's algo)
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Iterator

import numpy as np

from ..frames import SensorFrame


def ble_lines(frames: Iterable[SensorFrame], motion_invalid: float = 0.6,
              seed: int = 0) -> Iterator[str]:
    rng = np.random.default_rng([seed, 5])
    next_v = 1.0
    for f in frames:
        imu = f.imu["limb_moving"]
        step = max(imu.fs // 25, 1)
        for k in range(0, len(imu.acc_g), step):
            ax, ay, az = imu.acc_g[k]
            pitch = math.degrees(math.atan2(-ax, math.hypot(ay, az)))
            roll = math.degrees(math.atan2(ay, az))
            ms = int((imu.t0 + k / imu.fs) * 1000)
            yield f"I,{ms},{pitch:.1f},{roll:.1f}"
        while f.truth is not None and next_v <= f.t1:
            tr = f.truth
            finger = not tr.finger_off
            hr_ok = finger and tr.hr >= 20 and tr.motion < motion_invalid
            sp_ok = hr_ok and tr.spo2 > 50
            hr = int(round(tr.hr + rng.normal(0, 1.0))) if hr_ok else -999
            sp = int(round(tr.spo2 + rng.normal(0, 0.5))) if sp_ok else -999
            yield f"V,{hr},{int(hr_ok)},{sp},{int(sp_ok)},{int(finger)}"
            next_v += 1.0


class SmallBandEmulator:
    """Protocol v2 (docs/05): Q quaternion, V vitals + perfusion, B battery. Accepts the same
    commands as the firmware (RATE,10|25|50  VIB,ms  CAL) so the edge's command path is testable.
    """

    def __init__(self, motion_invalid: float = 0.6, seed: int = 0, battery_pct: float = 100.0,
                 battery_drain_per_min: float = 0.1):
        self.rng = np.random.default_rng([seed, 6])
        self.motion_invalid = motion_invalid
        self.rate_hz = 10
        self.commands: list[str] = []
        self.connected = True
        self.battery = battery_pct
        self.drain = battery_drain_per_min
        self._next_v = 1.0
        self._next_b = 0.0
        self._next_q = 0.0

    def command(self, text: str) -> None:
        self.commands.append(text)
        if text.startswith("RATE,"):
            r = int(text.split(",")[1])
            if r in (10, 25, 50):
                self.rate_hz = r

    def feed(self, f: SensorFrame) -> list[str]:
        if not self.connected:
            return []
        out: list[str] = []
        imu = f.imu["limb_moving"]
        period = 1.0 / self.rate_hz
        while self._next_q < f.t1:
            k = min(max(int(round((self._next_q - imu.t0) * imu.fs)), 0), len(imu.acc_g) - 1)
            w, x, y, z = imu.quat_wxyz[k]
            out.append(f"Q,{int(self._next_q * 1000)},{w:.4f},{x:.4f},{y:.4f},{z:.4f}")
            self._next_q += period
        tr = f.truth
        while tr is not None and self._next_v <= f.t1:
            finger = not tr.finger_off
            hr_ok = finger and tr.hr >= 20 and tr.motion < self.motion_invalid
            sp_ok = hr_ok and tr.spo2 > 50
            hr = int(round(tr.hr + self.rng.normal(0, 1.0))) if hr_ok else -999
            sp = int(round(tr.spo2 + self.rng.normal(0, 0.5))) if sp_ok else -999
            perf = 2.0 if hr_ok else 0.0
            out.append(f"V,{hr},{int(hr_ok)},{sp},{int(sp_ok)},{int(finger)},{perf:.1f}")
            self._next_v += 1.0
        while self._next_b <= f.t1:
            out.append(f"B,{max(0, int(round(self.battery - self.drain * self._next_b / 60)))}")
            self._next_b += 10.0
        return out

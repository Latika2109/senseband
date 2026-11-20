"""SimulatedSensors: turns a scenario into a stream of SensorFrames."""
from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Iterator
from typing import Any

import numpy as np

from ..frames import GripChunk, SensorFrame, TempSample, Truth
from .config import load_config
from .ecg import EcgGenerator
from .heart import BeatSchedule
from .imu import ImuGenerator
from .ppg import PpgGenerator
from .scenario import Timeline, load_timeline


def _idx_range(t0: float, t1: float, fs: float) -> tuple[int, int]:
    """Sample indices k (time k/fs) falling in [t0, t1) without float drift."""
    return math.ceil(t0 * fs - 1e-9), math.ceil(t1 * fs - 1e-9)


class Simulator:
    def __init__(self, scenario: str | Timeline, cfg: dict[str, Any] | None = None,
                 seed: int | None = None):
        self.cfg = cfg or load_config()
        self.timeline = (scenario if isinstance(scenario, Timeline)
                         else load_timeline(scenario, self.cfg, seed))
        self.seed = self.timeline.seed
        beats = BeatSchedule(self.timeline, self.cfg, np.random.default_rng([self.seed, 0]))
        self.beats = beats
        self.ecg = EcgGenerator(self.timeline, self.cfg, beats, self.seed)
        self.ppg = PpgGenerator(self.timeline, self.cfg, beats, self.seed)
        self.imu = ImuGenerator(self.timeline, self.cfg, self.seed)
        self._rng = np.random.default_rng([self.seed, 4])
        cal = np.array(self.cfg["grip"]["cal"], dtype=float)
        self._cal_v, self._cal_kg = cal[:, 0], cal[:, 1]
        self._k = 0
        self._grip_req: tuple[float, float] | None = None   # (start_t, peak_kg)

    def request_grip(self, peak_kg: float = 22.0) -> None:
        """On-demand grip squeeze starting at the next frame (for the service's grip test)."""
        self._grip_req = (self._k * float(self.cfg["frame_s"]), peak_kg)

    def cancel_grip(self) -> None:
        self._grip_req = None

    @property
    def duration_s(self) -> float:
        return self.timeline.duration_s

    # ---- frame generation ---------------------------------------------------------------
    def next_frame(self) -> SensorFrame:
        rates = self.cfg["rates_hz"]
        fr = float(self.cfg["frame_s"])
        t0, t1 = self._k * fr, (self._k + 1) * fr
        self._k += 1
        a, b = _idx_range(t0, t1, rates["temp"])
        temp = [self._temp(k / rates["temp"]) for k in range(a, b)]
        ga, gb = _idx_range(t0, t1, rates["grip"])
        grip = self._grip(ga, gb)
        frame = SensorFrame(
            t0=t0, t1=t1,
            ecg=self.ecg.chunk(*_idx_range(t0, t1, rates["ecg"])),
            imu=self.imu.chunks(*_idx_range(t0, t1, rates["imu"])),
            ppg=self.ppg.chunk(*_idx_range(t0, t1, rates["ppg"])),
            temp=temp, grip=grip, truth=self._truth(t0, t1))
        return frame

    def frames(self, duration_s: float | None = None) -> Iterator[SensorFrame]:
        n = int(round((duration_s or self.duration_s) / float(self.cfg["frame_s"])))
        for _ in range(n):
            yield self.next_frame()

    async def stream(self, speed: float = 1.0, duration_s: float | None = None
                     ) -> AsyncIterator[SensorFrame]:
        """Async stream; speed=1 is real time, speed=0 (or inf) is as fast as possible."""
        period = float(self.cfg["frame_s"]) / speed if speed and math.isfinite(speed) else 0.0
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        for frame in self.frames(duration_s):
            yield frame
            nxt += period
            await asyncio.sleep(max(0.0, nxt - loop.time()) if period else 0)

    # ---- slow sensors -------------------------------------------------------------------
    def _temp(self, t: float) -> TempSample:
        c = self.cfg["temp"]
        v = float(self.timeline.num("temp", t)[0])
        v += c["drift_c"] * math.sin(2 * math.pi * t / c["drift_period_s"])
        v += float(self._rng.normal(0, c["sensor_noise_c"]))
        q = c["resolution_c"]
        return TempSample(t, round(v / q) * q)

    def _grip(self, i0: int, i1: int) -> GripChunk | None:
        fs = self.cfg["rates_hz"]["grip"]
        if i1 <= i0:
            return None
        t = np.arange(i0, i1) / fs
        kg = self.timeline.grip_kg(t)
        if kg is None and self._grip_req is not None:
            t0, peak = self._grip_req
            tau = t - t0 - 1.0                                   # squeeze begins 1 s after the request
            shape = np.clip(np.minimum(tau / 1.5, (3.5 - tau) / 1.5), 0.0, 1.0)
            kg = peak * shape
        if kg is None:
            return None
        volts = np.interp(kg, self._cal_kg, self._cal_v)
        volts = np.clip(volts + self._rng.normal(0, self.cfg["grip"]["noise_v"], len(t)), 0, 3.3)
        return GripChunk(fs, i0 / fs, volts)

    def _truth(self, t0: float, t1: float) -> Truth:
        tl = self.timeline
        t = np.array([t1 - 1e-6])
        i = int(tl.index(t)[0])
        seg = tl.segments[i]
        joint = float(tl.joint_angle(t)[0]) if seg.p.get("joint") else None
        ev = next((k for te, k in tl.events if t0 <= te < t1), None)
        return Truth(
            t=t1, segment=i, label=str(seg.p["label"]), activity=seg.p["activity"],
            posture=seg.p["posture"], hr=float(tl.num("hr", t)[0]),
            rhythm=seg.p["rhythm"], spo2=float(tl.spo2(t)[0]),
            temp_c=float(tl.num("temp", t)[0]), motion=float(tl.num("motion", t)[0]),
            lead_off=bool(seg.p["lead_off"]), finger_off=bool(seg.p["finger_off"]),
            ecg_noise=float(tl.num("ecg_noise", t)[0]), joint_angle_deg=joint, event=ev)

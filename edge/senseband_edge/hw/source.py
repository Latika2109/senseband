"""Sensor sources for the edge service: simulator (laptop) or real hardware (Raspberry Pi).

Both yield `Bundle`s every frame_s seconds so the pipeline is identical in both modes.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from .frames import SensorFrame
from .sim import Simulator
from .sim.ble_lines import SmallBandEmulator


@dataclass
class Bundle:
    frame: SensorFrame
    ble_lines: list[str] = field(default_factory=list)
    lo_pins: tuple[bool, bool] | None = None


class SensorSource(Protocol):
    name: str

    def stream(self) -> AsyncIterator[Bundle]: ...
    def send_band_command(self, text: str) -> None: ...
    def set_grip(self, active: bool) -> None: ...
    def set_ecg(self, active: bool) -> None: ...
    def health(self) -> dict: ...
    def close(self) -> None: ...


class SimSource:
    """Simulated sensors + simulated small band. speed=1 real time, 0 = as fast as possible."""

    name = "sim"

    def __init__(self, scenario: str, speed: float = 1.0, loop: bool = False,
                 seed: int | None = None):
        self.scenario, self.speed, self.loop, self.seed = scenario, speed, loop, seed
        self.band = SmallBandEmulator()
        self.sim = Simulator(scenario, seed=seed)
        self.t_offset = 0.0           # keeps time increasing when the scenario loops
        self.ecg_on = True
        self.finished = False

    @property
    def duration_s(self) -> float:
        return self.sim.duration_s

    async def stream(self) -> AsyncIterator[Bundle]:
        while True:
            async for f in self.sim.stream(speed=self.speed):
                if self.t_offset:
                    f = _shift(f, self.t_offset)
                if not self.ecg_on:                                # simulated ECG front-end switched off
                    f = _without_ecg(f)
                yield Bundle(f, self.band.feed(f))
            if not self.loop:
                self.finished = True
                return
            self.t_offset += self.sim.duration_s
            self.sim = Simulator(self.scenario, seed=self.seed)
            await asyncio.sleep(0)

    def send_band_command(self, text: str) -> None:
        self.band.command(text)

    def set_ecg(self, active: bool) -> None:
        self.ecg_on = active

    def set_grip(self, active: bool) -> None:
        if active:
            self.sim.request_grip()
        else:
            self.sim.cancel_grip()

    def health(self) -> dict:
        return {"source": "sim", "scenario": self.scenario, "ble": self.band.connected,
                "battery_pct": self.band.battery}

    def close(self) -> None:
        pass


def _without_ecg(f: SensorFrame) -> SensorFrame:
    from dataclasses import replace

    import numpy as np
    return replace(f, ecg=replace(f.ecg, mv=np.zeros(0), lead_off=np.zeros(0, bool)))


def _shift(f: SensorFrame, dt: float) -> SensorFrame:
    from dataclasses import replace
    return replace(
        f, t0=f.t0 + dt, t1=f.t1 + dt,
        ecg=replace(f.ecg, t0=f.ecg.t0 + dt),
        imu={k: replace(v, t0=v.t0 + dt) for k, v in f.imu.items()},
        ppg=replace(f.ppg, t0=f.ppg.t0 + dt),
        temp=[replace(s, t=s.t + dt) for s in f.temp],
        grip=None if f.grip is None else replace(f.grip, t0=f.grip.t0 + dt),
        truth=None if f.truth is None else replace(f.truth, t=f.truth.t + dt))

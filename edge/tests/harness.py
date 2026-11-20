"""Run a simulator scenario through the Engine at max speed (data-clock, no sleeping)."""
from __future__ import annotations

from dataclasses import dataclass

from senseband_edge.alerts.sms import MockSms
from senseband_edge.config import Settings, load_settings
from senseband_edge.context.engine import Engine
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.sim import Simulator
from senseband_edge.hw.sim.ble_lines import SmallBandEmulator
from senseband_edge.hw.source import Bundle
from senseband_edge.storage.db import Store

BASE_MS = 1_700_000_000_000


def make_settings(**over) -> Settings:  # noqa: ANN003
    o = {"mode": "sim", "models": {"enabled": False}, "patient": {"id": "patient01"}, "sms": {"contacts": ["+910000000001"], "retry_delay_s": 0}}
    for k, v in over.items():
        o[k] = {**o.get(k, {}), **v} if isinstance(v, dict) else v
    return load_settings(o)


@dataclass
class Run:
    engine: Engine
    sms: MockSms
    io: SimIO
    store: Store
    vitals: list[dict]
    events: list[dict]
    msgs: list[dict]


def run_scenario(name: str, seconds: float | None = None, sms: MockSms | None = None,
                 on_frame=None, settings: Settings | None = None, seed: int | None = None,
                 scenario: dict | None = None, strip_ecg_when_paused: bool = False) -> Run:  # noqa: ANN001
    cfg = settings or make_settings()
    store, io, sms = Store(":memory:"), SimIO(), sms or MockSms()
    msgs: list[dict] = []
    eng = Engine(cfg, store, sms, io, base_ms=BASE_MS, sleep=lambda s: None, emit=msgs.append)
    if scenario is not None:
        from senseband_edge.hw.sim import load_config
        from senseband_edge.hw.sim.scenario import Timeline
        sim = Simulator(Timeline(scenario, load_config(), seed), load_config(), seed)
    else:
        sim = Simulator(name, seed=seed)
    band = SmallBandEmulator()
    eng.rehab.band_command = band.command
    eng.rehab.grip_hook = lambda on: sim.request_grip() if on else sim.cancel_grip()
    eng.band_emulator = band  # type: ignore[attr-defined]
    for f in sim.frames(seconds):
        if strip_ecg_when_paused and eng.power.paused:      # the simulated AD8232 is off: no ECG samples arrive
            from senseband_edge.hw.source import _without_ecg
            f = _without_ecg(f)
        eng.process(Bundle(f, band.feed(f)))
        if on_frame:
            on_frame(eng, f)
    eng.flush()
    return Run(eng, sms, io, store, [m for m in msgs if m["t"] == "vitals"],
               [m for m in msgs if m["t"] == "event"], msgs)


def kinds(run: Run, level: str | None = None) -> list[str]:
    return [a.kind for a in run.engine.alerts.alerts if level in (None, a.level)]

"""Headless run of a long scenario through the real engine + storage, then a PDF report.

    python edge/scripts/run_day.py day_in_life /tmp/day.db /tmp/day.pdf

Used for the Milestone 6 acceptance run (24 simulated hours). No sleeping: data-clock only."""
from __future__ import annotations

import sys
import time

sys.path[:0] = ["edge", "ml"]

from senseband_edge.alerts.sms import MockSms  # noqa: E402
from senseband_edge.config import load_settings  # noqa: E402
from senseband_edge.context.engine import Engine  # noqa: E402
from senseband_edge.hw.io import SimIO  # noqa: E402
from senseband_edge.hw.sim import Simulator  # noqa: E402
from senseband_edge.hw.sim.ble_lines import SmallBandEmulator  # noqa: E402
from senseband_edge.hw.source import Bundle  # noqa: E402
from senseband_edge.rehab.service import RecoveryService  # noqa: E402
from senseband_edge.reports.builder import build_report  # noqa: E402
from senseband_edge.reports.pdf import render_pdf  # noqa: E402
from senseband_edge.storage.db import Store  # noqa: E402


def main(scenario: str, db: str, pdf: str) -> int:
    cfg = load_settings({"mode": "sim", "patient": {"id": "patient01", "name": "Simulated Patient"}})
    store = Store(db)
    base = int(time.time() * 1000) - 86_400_000
    store.upsert("patients", {"id": "patient01", "name": "Simulated Patient", "conditions": [], "medications": [],
                              "emergency_contacts": []})
    eng = Engine(cfg, store, MockSms(), SimIO(), base_ms=base, sleep=lambda s: None)
    sim, band = Simulator(scenario), SmallBandEmulator()
    t0 = time.time()
    n = 0
    for f in sim.frames():
        eng.process(Bundle(f, band.feed(f)))
        n += 1
        if n % 25_000 == 0:
            print(f"  simulated {f.t1 / 3600:5.1f} h  ({time.time() - t0:5.0f} s wall)", flush=True)
    eng.flush()
    rep = build_report(store, cfg, RecoveryService(cfg, store), base, base + int(sim.duration_s * 1000) + 60_000)
    open(pdf, "wb").write(render_pdf(rep))
    v = rep.vitals
    print(f"report: {v['minutes_with_data']} minutes, coverage {rep.data_quality['coverage_pct']}%, "
          f"HR {v['hr_min']}/{v['hr_avg']}/{v['hr_max']}, resting {v['hr_resting_avg']}, SpO2 min {v['spo2_min']}, "
          f"sleep {rep.sleep['minutes'] / 60:.1f} h, activity {rep.activity_minutes}, events {rep.event_counts}, "
          f"errors {eng.stats['errors']}")
    print("wrote", pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))

"""CLI:  python -m senseband_edge.hw.sim --scenario jogging --duration 600 [--speed 0]"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

import numpy as np

from . import Simulator, list_scenarios
from .analysis import hr_from_ecg
from .ble_lines import ble_lines
from .config import load_scenario_dict


async def run(a: argparse.Namespace) -> int:
    sim = Simulator(a.scenario, seed=a.seed)
    dur = min(a.duration, sim.duration_s) if a.duration else sim.duration_s
    print(f"scenario '{sim.timeline.name}': {sim.timeline.description}")
    print(f"streaming {dur:.0f} s at speed {a.speed or 'max'} "
          f"({len(sim.timeline.segments)} segments)\n")
    ecg_buf: list[np.ndarray] = []
    n = {"ecg": 0, "imu": 0, "ppg": 0, "temp": 0, "grip": 0}
    nan = 0
    hr_err: list[float] = []
    next_print, t_wall = 0.0, time.time()
    async for f in sim.stream(speed=a.speed, duration_s=dur):
        n["ecg"] += len(f.ecg.mv)
        n["imu"] += len(f.imu["waist"].acc_g)
        n["ppg"] += len(f.ppg.ir)
        n["temp"] += len(f.temp)
        n["grip"] += 0 if f.grip is None else len(f.grip.volts)
        arrays = [f.ecg.mv, f.ppg.ir, *(c.acc_g for c in f.imu.values()),
                  *(c.gyro_dps for c in f.imu.values()), *(c.quat_wxyz for c in f.imu.values())]
        nan += sum(int(np.isnan(x).sum()) for x in arrays)
        ecg_buf.append(f.ecg.mv)
        if f.t1 >= next_print:
            next_print += a.every
            win = np.concatenate(ecg_buf)[-10 * f.ecg.fs:]
            hr = hr_from_ecg(win, f.ecg.fs)
            tr = f.truth
            if tr.hr >= 20 and not tr.lead_off and tr.ecg_noise == 0 and f.t1 >= 15 \
                    and not np.isnan(hr):
                hr_err.append(abs(hr - tr.hr))
            acc = np.linalg.norm(f.imu["waist"].acc_g, axis=1).mean()
            flags = ("LEAD-OFF " if tr.lead_off else "") + ("FINGER-OFF " if tr.finger_off else "")
            ang = "" if tr.joint_angle_deg is None else f" joint={tr.joint_angle_deg:6.1f}deg"
            print(f"t={f.t1:6.0f}s {tr.activity:<15} true_hr={tr.hr:5.1f} ecg_hr={hr:5.1f} "
                  f"spo2={tr.spo2:5.1f} temp={tr.temp_c:4.1f} |acc|={acc:4.2f}g{ang} {flags}"
                  f"{'  <<' + tr.event.upper() + '>>' if tr.event else ''}")
        ecg_buf[:] = [np.concatenate(ecg_buf)[-12 * f.ecg.fs:]]
    wall = time.time() - t_wall
    n_lines = sum(1 for _ in ble_lines(Simulator(a.scenario, seed=a.seed).frames(min(dur, 30))))
    print("\n--- summary ---")
    print(f"simulated {dur:.0f} s in {wall:.1f} s wall time; samples: {n}")
    print(f"NaN values: {nan}; BLE-style lines in first 30 s: {n_lines}")
    if hr_err:
        print(f"ECG-derived HR vs truth (clean segments): median abs error "
              f"{np.median(hr_err):.1f} bpm over {len(hr_err)} windows")
    return 0 if nan == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="SenseBand hardware simulator")
    ap.add_argument("--scenario", default="resting")
    ap.add_argument("--duration", type=float, default=0, help="seconds (0 = whole scenario)")
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 0 = max speed")
    ap.add_argument("--every", type=float, default=10.0, help="print a status line every N s")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    a = ap.parse_args()
    if a.list:
        for s in list_scenarios():
            print(f"{s:<22} {load_scenario_dict(s).get('description', '')}")
        return 0
    try:
        return asyncio.run(run(a))
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

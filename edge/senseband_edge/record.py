"""'Record training data' mode (docs/06 M5): capture labelled raw sensor data to a .npz for ml/senseband_train.

A caregiver/physio starts it with a label ("walking", "sitting", "fall_simulated", ...), the person does the
activity, and `stop` writes `<recordings_dir>/<utc time>_<label>.npz`. RAM is capped (max_recording_s) so a
forgotten recording cannot exhaust the Pi; the file is the only thing kept.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import numpy as np

from .hw.frames import SensorFrame

LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class RecorderError(Exception):
    pass


class Recorder:
    def __init__(self, out_dir: Path, max_seconds: float):
        self.dir, self.max_s = out_dir, max_seconds
        self.active = False
        self.label = ""
        self._t0 = 0.0
        self._start_wall = 0.0
        self._bufs: dict[str, list[np.ndarray]] = {}
        self._meta: dict[str, Any] = {}
        self.seconds = 0.0

    def start(self, label: str, now: float, meta: dict[str, Any] | None = None) -> None:
        if self.active:
            raise RecorderError("already recording")
        if not LABEL_RE.match(label):
            raise RecorderError("label must be 1-40 chars: a-z, 0-9, _ or -")
        self.active, self.label, self._t0, self.seconds = True, label, now, 0.0
        self._start_wall = time.time()
        self._bufs = {k: [] for k in ("ecg_mv", "ecg_lead_off", "acc_g", "gyro_dps", "ppg_ir", "ppg_red", "temp_c")}
        self._meta = {"label": label, "ecg_fs": 250, "imu_fs": 50, "ppg_fs": 25, **(meta or {})}

    def add(self, f: SensorFrame) -> bool:
        """Append one frame. Returns False (and keeps the data) once the time cap is reached."""
        if not self.active:
            return False
        self.seconds = f.t1 - self._t0
        w = f.imu["waist"]
        self._bufs["ecg_mv"].append(f.ecg.mv.astype(np.float32))
        self._bufs["ecg_lead_off"].append(f.ecg.lead_off.astype(np.int8))
        self._bufs["acc_g"].append(w.acc_g.astype(np.float32))
        self._bufs["gyro_dps"].append(w.gyro_dps.astype(np.float32))
        self._bufs["ppg_ir"].append(f.ppg.ir.astype(np.int32))
        self._bufs["ppg_red"].append(f.ppg.red.astype(np.int32))
        self._bufs["temp_c"].append(np.array([s.celsius for s in f.temp], dtype=np.float32))
        return self.seconds < self.max_s

    def stop(self) -> dict[str, Any]:
        if not self.active:
            raise RecorderError("not recording")
        self.active = False
        arrays = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in self._bufs.items()}
        self._bufs = {}
        if arrays["ecg_mv"].size == 0 and arrays["acc_g"].size == 0:
            raise RecorderError("no data was received while recording")
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self._start_wall))
        path = self.dir / f"{stamp}_{self.label}.npz"
        np.savez_compressed(path, **arrays, **{f"meta_{k}": np.asarray(v) for k, v in self._meta.items()})
        return {"path": str(path), "label": self.label, "seconds": round(self.seconds, 1), "bytes": path.stat().st_size}

    def list(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        return [{"file": p.name, "bytes": p.stat().st_size} for p in sorted(self.dir.glob("*.npz"))]

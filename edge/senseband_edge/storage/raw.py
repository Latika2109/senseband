"""Rolling raw store (docs/02 rule 3): the last N days of raw ECG/IMU stay on the band, one chunk per minute.

Off by default (SD-card wear, space). Chunks are int16-quantised and zlib-compressed into the `raw_chunks`
table; `prune` deletes anything older than the retention window. Raw data is never uploaded automatically.
"""
from __future__ import annotations

import time
import zlib

import numpy as np

from ..hw.frames import SensorFrame
from .db import Store


class RawStore:
    def __init__(self, store: Store, days: int):
        self.store, self.days = store, days
        self._minute: int | None = None
        self._ecg: list[np.ndarray] = []
        self._acc: list[np.ndarray] = []
        self._last_prune = 0.0

    def add(self, f: SensorFrame, epoch_ms: int) -> None:
        minute = epoch_ms // 60000
        if self._minute is not None and minute != self._minute:
            self.flush()
        self._minute = minute
        self._ecg.append(f.ecg.mv)
        self._acc.append(f.imu["waist"].acc_g)

    def flush(self) -> None:
        if self._minute is None or not self._ecg:
            return
        ts = self._minute * 60000
        ecg = np.clip(np.concatenate(self._ecg) * 1000, -32768, 32767).astype("<i2")      # microvolt-ish, int16
        acc = np.clip(np.concatenate(self._acc) * 1000, -32768, 32767).astype("<i2")      # milli-g
        with self.store.lock:
            self.store.db.execute("INSERT INTO raw_chunks(ts, stream, payload) VALUES(?,?,?)",
                                  (ts, "ecg_mv_x1000_i16", zlib.compress(ecg.tobytes(), 6)))
            self.store.db.execute("INSERT INTO raw_chunks(ts, stream, payload) VALUES(?,?,?)",
                                  (ts, "acc_mg_i16", zlib.compress(acc.tobytes(), 6)))
        self._ecg, self._acc = [], []
        if time.time() - self._last_prune > 3600:
            self.prune()

    def prune(self, now_ms: int | None = None) -> int:
        self._last_prune = time.time()
        cutoff = (now_ms if now_ms is not None else int(time.time() * 1000)) - self.days * 86_400_000
        with self.store.lock:
            cur = self.store.db.execute("DELETE FROM raw_chunks WHERE ts < ?", (cutoff,))
            return cur.rowcount

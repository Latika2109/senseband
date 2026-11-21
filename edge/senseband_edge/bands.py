"""Wi-Fi ESP32 small bands: the hub that receives their readings and hands them to the pipeline.

Protocol = the BLE text protocol over HTTP: an ESP32 POSTs a batch of lines every ~0.5 s to /api/band/ingest
(`Q,ms,w,x,y,z` quaternion, `V,hr,hrValid,spo2,spo2Valid,finger,perfusion` vitals, `B,pct` battery, `A,ms,ax,ay,az,gx,gy,gz`
raw waist IMU in g and deg/s) and the reply carries any pending commands (`RATE,50`, `VIB,120`, `CAL`).

Security: the endpoint is closed until SENSEBAND_BAND_TOKEN is set; the device sends it in the X-Band-Token header
(constant-time compare). Input is size-limited and every line must match a strict numeric pattern.
"""
from __future__ import annotations

import hmac
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import BandsCfg, Quality
from .pipeline.blelink import SmallBandParser

DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
LINE = re.compile(r"^[QVBIA],[-+0-9.eE,]{1,100}$")
COMMAND = re.compile(r"^(RATE,(10|25|50)|VIB,\d{1,4}|CAL)$")
MAX_LINES = 1500
MAX_IMU_BUFFER = 50 * 20          # 20 s of waist IMU


@dataclass
class Device:
    id: str
    first_seen: float
    last_seen: float
    ip: str | None = None
    lines: int = 0
    bad_lines: int = 0
    batches: int = 0
    parser: SmallBandParser | None = None
    commands: deque[str] = field(default_factory=lambda: deque(maxlen=20))


@dataclass
class IngestResult:
    accepted: int = 0
    rejected: int = 0
    commands: list[str] = field(default_factory=list)


class HubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class BandHub:
    def __init__(self, cfg: BandsCfg, quality: Quality, token: str | None = None):
        self.cfg, self.quality = cfg, quality
        self.token = token if token is not None else os.environ.get("SENSEBAND_BAND_TOKEN", "")
        self.devices: dict[str, Device] = {}
        self._primary: deque[str] = deque(maxlen=4000)
        self._imu: deque[tuple[float, float, float, float, float, float]] = deque(maxlen=MAX_IMU_BUFFER)
        self.lock = threading.Lock()

    # ---- auth ------------------------------------------------------------------------------------------
    @property
    def open_for_business(self) -> bool:
        return self.cfg.enabled and len(self.token) >= 16

    def authorize(self, presented: str | None) -> None:
        if not self.cfg.enabled:
            raise HubError(404, "Wi-Fi band ingest is disabled (bands.enabled: false)")
        if len(self.token) < 16:
            raise HubError(403, "band ingest is closed: set SENSEBAND_BAND_TOKEN (at least 16 characters) on the Pi")
        if not presented or not hmac.compare_digest(presented.encode(), self.token.encode()):
            raise HubError(401, "wrong or missing X-Band-Token")

    # ---- ingest ----------------------------------------------------------------------------------------
    def ingest(self, device_id: str, body: str, ip: str | None = None, now: float | None = None) -> IngestResult:
        if not DEVICE_ID.match(device_id or ""):
            raise HubError(422, "X-Band-Id must be 1-16 characters: letters, digits, _ or -")
        if len(body.encode()) > self.cfg.max_body_bytes:
            raise HubError(413, "batch too large")
        lines = body.splitlines()
        if len(lines) > MAX_LINES:
            raise HubError(413, "too many lines in one batch")
        now = time.time() if now is None else now
        res = IngestResult()
        with self.lock:
            dev = self.devices.get(device_id)
            if dev is None:
                if len(self.devices) >= self.cfg.max_devices:
                    raise HubError(429, "too many devices")
                dev = self.devices[device_id] = Device(device_id, now, now, ip, parser=SmallBandParser(self.quality))
            dev.last_seen, dev.ip = now, ip or dev.ip
            dev.batches += 1
            is_primary = device_id == self.cfg.primary
            is_waist = bool(self.cfg.waist_imu_from) and device_id == self.cfg.waist_imu_from
            for raw in lines:
                line = raw.strip()
                if not line:
                    continue
                if not LINE.match(line):
                    res.rejected += 1
                    dev.bad_lines += 1
                    continue
                if line[0] == "A":
                    try:
                        v = tuple(float(x) for x in line.split(",")[2:8])
                    except ValueError:
                        v = ()
                    if len(v) != 6 or not np.isfinite(v).all():
                        res.rejected += 1
                        dev.bad_lines += 1
                        continue
                    if is_waist:
                        self._imu.append(v)  # type: ignore[arg-type]
                else:
                    if dev.parser is None or dev.parser.feed(line, now) is None:
                        res.rejected += 1
                        dev.bad_lines += 1
                        continue
                    if is_primary:
                        self._primary.append(line)
                res.accepted += 1
                dev.lines += 1
            while dev.commands:
                res.commands.append(dev.commands.popleft())
        return res

    # ---- consumed by the service loop ----------------------------------------------------------------------
    def drain_primary(self) -> list[str]:
        with self.lock:
            out = list(self._primary)
            self._primary.clear()
        return out

    def drain_imu(self) -> tuple[np.ndarray, np.ndarray] | None:
        """(acc_g (n,3), gyro_dps (n,3)) received since the last call, or None if nothing arrived."""
        with self.lock:
            if not self._imu:
                return None
            a = np.asarray(self._imu, dtype=np.float64)
            self._imu.clear()
        return a[:, :3], a[:, 3:]

    # ---- commands to the devices -----------------------------------------------------------------------------
    def queue_command(self, text: str, device_id: str | None = None) -> bool:
        if not COMMAND.match(text):
            return False
        with self.lock:
            target = device_id or self.cfg.primary
            dev = self.devices.get(target)
            if dev is None:
                return False
            if text not in dev.commands or not text.startswith("RATE"):
                dev.commands.append(text)
        return True

    # ---- status ------------------------------------------------------------------------------------------------
    def status(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self.lock:
            devs = []
            for d in self.devices.values():
                st = d.parser.state if d.parser else None
                devs.append({"id": d.id, "ip": d.ip, "connected": now - d.last_seen <= self.cfg.stale_s,
                             "last_seen_s_ago": round(now - d.last_seen, 1), "lines": d.lines, "bad_lines": d.bad_lines,
                             "role": ("primary" if d.id == self.cfg.primary else "extra")
                             + ("+waist_imu" if self.cfg.waist_imu_from == d.id else ""),
                             "battery_pct": st.battery_pct if st else None, "finger": st.finger if st else None,
                             "hr": st.hr if st else None, "spo2": st.spo2 if st else None,
                             "pending_commands": len(d.commands)})
        return {"enabled": self.cfg.enabled, "open": self.open_for_business, "primary": self.cfg.primary,
                "waist_imu_from": self.cfg.waist_imu_from or None, "devices": devs}

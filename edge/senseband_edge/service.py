"""EdgeService: wires source -> engine -> storage/alerts/WebSocket. One asyncio process."""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import resource
import socket
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from .alerts.manager import Alert
from .alerts.sms import SmsGateway, send_with_retries
from .alerts.twilio import make_sms
from .bands import BandHub
from .config import Settings
from .context.engine import Engine
from .hw.frames import ImuChunk
from .hw.io import BandIO, GpioIO, SimIO
from .hw.source import Bundle, SensorSource, SimSource
from .models.personal import PersonalHr
from .rehab.service import RecoveryService
from .storage.db import Store
from .sync.transport import HttpTransport
from .sync.worker import SyncWorker


class WsHub:
    """Fan-out of live JSON messages. Slow clients drop their oldest messages."""

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=300)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self.clients.discard(q)

    def publish(self, msg: dict[str, Any]) -> None:
        for q in list(self.clients):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(msg)


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


class EdgeService:
    def __init__(self, cfg: Settings, source: SensorSource, sms: SmsGateway | None = None,
                 io: BandIO | None = None, store: Store | None = None):
        self.cfg, self.source = cfg, source
        self.store = store or Store(cfg.db_path)
        if cfg.patient.id == "auto":                 # unique per band: it becomes the cloud patient key
            pid = self.store.state_get("patient_id")
            if not pid:
                pid = "p-" + uuid.uuid4().hex[:12]
                self.store.state_set("patient_id", pid)
            cfg.patient.id = pid
        self.sms = sms or make_sms(cfg)
        self.bands = BandHub(cfg.bands, cfg.quality)
        self.io = io or (SimIO() if cfg.is_sim else GpioIO(cfg.hardware))
        self.hub = WsHub()
        self.engine = Engine(cfg, self.store, self.sms, self.io, emit=self._emit)
        self.engine.sms_dispatch = self._dispatch_sms
        self.recovery = RecoveryService(cfg, self.store)
        self.engine.rehab.band_command = self._band_command
        self.engine.rehab.grip_hook = self.source.set_grip
        self.engine.ecg_control = self.source.set_ecg
        self.sync: SyncWorker | None = None
        url, key = cfg.cloud.url, cfg.cloud.device_key
        if not (cfg.cloud.enabled and url and key):          # a link made from the app is kept in the DB
            url, key = self.store.state_get("cloud_url") or "", self.store.state_get("cloud_key") or ""
        if url and key and (cfg.cloud.enabled or self.store.state_get("cloud_url")):
            self._make_sync(url, key)
        self.sync_task: asyncio.Task | None = None
        self.started = time.time()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self._net: tuple[float, bool] = (0.0, False)
        self.io.on_buttons(self._button_short, self._button_long)
        self.ensure_patient()
        self.ensure_program()

    def _make_sync(self, url: str, key: str) -> None:
        self.sync = SyncWorker(self.store, HttpTransport(url, key, self.cfg.cloud.timeout_s), self.cfg.cloud,
                               on_patient=self._apply_patient)

    def link_cloud(self, url: str, device_key: str) -> dict[str, Any]:
        """Store the cloud address + this band's secret key (from register_device) and start syncing."""
        if not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", url) or len(device_key) < 16:
            raise ValueError("a cloud URL and a device key are required")
        self.store.state_set("cloud_url", url.rstrip("/"))
        self.store.state_set("cloud_key", device_key)
        if self.sync_task:
            self.sync_task.cancel()
        self._make_sync(url.rstrip("/"), device_key)
        if self.loop is not None and self.sync is not None:
            self.sync_task = self.loop.create_task(self.sync.run(), name="edge-sync")
        return self.sync.status() if self.sync else {}

    def after_erase(self) -> None:
        """After an erase the band starts clean: no cloud link, an empty profile and a fresh default program."""
        self.unlink_cloud()
        self.store.state_set("patient_id", self.cfg.patient.id)
        self.engine.alerts.alerts.clear()
        self.engine.alerts.last_raised.clear()
        self.engine.baseline_hr = None
        self.engine.contacts = []
        self.engine.personal = PersonalHr()
        self.engine.rehab.angles.clear()
        self.engine.rehab.calibrated_at.clear()
        self.ensure_patient()
        self.ensure_program()

    def unlink_cloud(self) -> None:
        for k in ("cloud_url", "cloud_key", "pull_cursor"):
            self.store.state_set(k, "")
        if self.sync_task:
            self.sync_task.cancel()
        self.sync, self.sync_task = None, None

    # ---- life cycle -------------------------------------------------------------------------
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self._run(), name="edge-pipeline")
        if self.sync:
            self.sync_task = asyncio.create_task(self.sync.run(), name="edge-sync")

    async def stop(self) -> None:
        if self.sync_task:
            self.sync_task.cancel()
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.engine.flush()
        self.source.close()
        self.io.close()

    async def _run(self) -> None:
        last_status = 0.0
        ok_frames = 0
        async for bundle in self.source.stream():
            try:
                self.engine.process(self._merge_wifi(bundle))
                ok_frames += 1
                if ok_frames == 30:
                    self._mark_healthy()
            except Exception as e:  # noqa: BLE001 - a bad frame must never kill monitoring
                self.engine.stats["errors"] += 1
                print("pipeline error:", repr(e))
            if self.engine.now - last_status >= 5.0:
                last_status = self.engine.now
                self.hub.publish({"t": "status", **self.status()})
            await asyncio.sleep(0)               # let the API / WebSocket run

    def _band_command(self, text: str) -> None:
        """RATE/VIB/CAL go to the BLE small band and, queued, to the Wi-Fi band (it fetches them on its next POST)."""
        self.source.send_band_command(text)
        self.bands.queue_command(text)

    def _merge_wifi(self, bundle: Bundle) -> Bundle:
        """Add what the ESP32s sent over Wi-Fi since the last frame (primary band lines; optional waist IMU)."""
        extra = self.bands.drain_primary()
        if extra:
            bundle.ble_lines.extend(extra)
        if self.cfg.bands.waist_imu_from:
            f = bundle.frame
            got = self.bands.drain_imu()
            acc, gyro = got if got is not None else (np.zeros((0, 3)), np.zeros((0, 3)))
            quat = np.tile([1.0, 0.0, 0.0, 0.0], (len(acc), 1))
            waist = ImuChunk(50, f.t0, acc, gyro, quat)
            bundle = replace(bundle, frame=replace(f, imu={**f.imu, "waist": waist}))
        return bundle

    def _mark_healthy(self) -> None:
        """The OTA updater waits for this file after a restart; without it the update is rolled back."""
        path = os.environ.get("SENSEBAND_HEALTH_FILE")
        if path:
            ver = os.environ.get("SENSEBAND_VERSION", "")
            try:
                Path(path).write_text(ver)
            except OSError:
                pass

    # ---- SMS in a worker thread (the real modem blocks for seconds) -------------------------
    def _dispatch_sms(self, a: Alert) -> None:
        if self.loop is None:
            self.engine._send_sms_sync(a)
            return
        text = f"SenseBand {a.level.upper()} [{self.cfg.patient.name}]: {a.message}"
        contacts = self.engine.contacts or self.cfg.sms.contacts

        def job() -> None:
            res = send_with_retries(self.sms, contacts, text, self.cfg.sms.retries,
                                    self.cfg.sms.retry_delay_s)
            assert self.loop is not None
            self.loop.call_soon_threadsafe(self.engine.alerts.sms_done, a, len(res.delivered))

        self.loop.run_in_executor(None, job)

    def _emit(self, msg: dict[str, Any]) -> None:
        self.hub.publish(msg)

    # ---- buttons ----------------------------------------------------------------------------
    def _button_short(self) -> None:
        self.engine.i_am_ok("button")

    def _button_long(self) -> None:
        self.engine.sos()

    # ---- patient / programs -----------------------------------------------------------------
    def ensure_patient(self) -> dict[str, Any]:
        p = self.store.get("patients", self.cfg.patient.id)
        if p is None:
            p = self.store.upsert("patients", {
                "id": self.cfg.patient.id, "name": self.cfg.patient.name,
                "resting_hr": self.cfg.patient.resting_hr, "conditions": [], "medications": [],
                "emergency_contacts": [{"name": "Contact", "phone": c, "relation": ""}
                                       for c in self.cfg.sms.contacts],
                "created_at": int(time.time() * 1000)})
            p = self.store.get("patients", self.cfg.patient.id) or p
        self._apply_patient(p)
        return p

    def _apply_patient(self, p: dict[str, Any]) -> None:
        contacts = p.get("emergency_contacts") or []
        self.engine.contacts = [c["phone"] for c in contacts if c.get("phone")]
        meds = " ".join(p.get("medications") or []).lower()
        self.engine.beta_blocker = (self.cfg.patient.beta_blocker or "beta" in meds
                                    or "metoprolol" in meds or "atenolol" in meds)
        if p.get("resting_hr"):
            self.engine.baseline_hr = float(p["resting_hr"])

    def update_patient(self, fields: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "dob", "sex", "height_cm", "weight_kg", "conditions", "medications",
                   "resting_hr", "emergency_contacts"}
        self.store.update("patients", self.cfg.patient.id,
                          {k: v for k, v in fields.items() if k in allowed})
        p = self.store.get("patients", self.cfg.patient.id) or {}
        self._apply_patient(p)
        return p

    def active_program(self) -> dict[str, Any] | None:
        rows = self.store.list("programs", "status='active'", order="start_ts DESC", limit=1)
        return rows[0] if rows else None

    def ensure_program(self) -> dict[str, Any]:
        p = self.active_program() or self.start_program("monitoring", "general", None)
        self.engine.program_id = p["id"]
        self.engine.learning_start_ms = p["start_ts"]
        return p

    def start_program(self, type_: str, purpose: str, duration_h: float | None,
                      settings: dict[str, Any] | None = None,
                      recovery_case: dict[str, Any] | None = None) -> dict[str, Any]:
        if type_ not in ("monitoring", "recovery"):
            raise ValueError("type must be 'monitoring' or 'recovery'")
        if type_ == "recovery" and not recovery_case:
            raise ValueError("a recovery program needs recovery_case (joint, side, injury)")
        if type_ == "recovery":                      # validate before touching the old program
            RecoveryService.validate_case(recovery_case or {})
        for old in self.store.list("programs", "status='active'"):
            self.store.update("programs", old["id"], {"status": "stopped"})
        now = int(time.time() * 1000)
        row = self.store.upsert("programs", {
            "patient_id": self.cfg.patient.id, "type": type_, "purpose": purpose,
            "start_ts": now, "planned_end_ts": None if duration_h is None else now + int(
                duration_h * 3600_000), "status": "active", "settings_json": settings or {}})
        self.engine.program_id = row["id"]
        self.engine.learning_start_ms = row["start_ts"]
        if type_ == "recovery":
            self.recovery.create_case(row["id"], recovery_case or {})
        return row

    def stop_program(self, program_id: str) -> dict[str, Any] | None:
        p = self.store.get("programs", program_id)
        if p is None:
            return None
        self.store.update("programs", program_id, {"status": "stopped"})
        return self.store.get("programs", program_id)

    # ---- status -----------------------------------------------------------------------------
    def internet(self) -> bool | None:
        if self.cfg.is_sim:
            return None                                   # unknown in simulation
        if time.time() - self._net[0] > 20:
            try:
                socket.create_connection(("8.8.8.8", 53), timeout=2).close()
                self._net = (time.time(), True)
            except OSError:
                self._net = (time.time(), False)
        return self._net[1]

    def status(self) -> dict[str, Any]:
        e = self.engine
        v = e.vitals
        return {
            "time": int(time.time() * 1000), "mode": self.source.name,
            "uptime_s": round(time.time() - self.started, 1), "data_time_s": round(e.now, 1),
            "sensors": {"ecg": e.ecg_res.quality, "ppg": (v.get("quality") or {}).get("ppg"),
                        "imu": e.act.result.activity != "unknown",
                        "temp": e.temp_c is not None},
            "band": e.band_status(), "source": self.source.health(),
            "sms": self.sms.status(), "internet": self.internet(), "bands": self.bands.status(),
            "sync_backlog": self.store.backlog(),
            "sync": self.sync.status() if self.sync else {"enabled": False, "backlog": self.store.backlog()},
            "program": self.active_program(),
            "alerts": {"countdown": [e.public(a) for a in e.alerts.pending_countdown],
                       "recent": [e.public(a) for a in e.alerts.recent(10)]},
            "learning_since_ts": (self.active_program() or {}).get("start_ts"),
            "learning_h": self.cfg.models.learning_period_h, "models": self.engine.models.status(),
            "buzzer": getattr(self.io, "buzzer_on", None),
            "rss_mb": round(rss_mb(), 1), "errors": e.stats["errors"],
        }


def build_service(cfg: Settings, scenario: str = "resting", speed: float = 1.0,
                  loop: bool = True) -> EdgeService:
    if cfg.is_sim:
        return EdgeService(cfg, SimSource(scenario, speed=speed, loop=loop))
    from .hw.hardware_source import HardwareSource
    return EdgeService(cfg, HardwareSource(cfg))

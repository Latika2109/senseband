"""Real sensors on the Raspberry Pi: ADS1115 ECG @250 Hz, MPU6050 @50 Hz, DS18B20, BLE band.

Runs sampler threads and yields one Bundle per frame_s, the same shape the simulator yields.
Any missing sensor degrades (empty chunk + health flag); the service keeps running.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator

import numpy as np

from ..config import Settings
from ..pipeline.madgwick import Madgwick
from .ble_link import SmallBandLink
from .frames import EcgChunk, GripChunk, ImuChunk, PpgChunk, SensorFrame, TempSample
from .source import Bundle

FRAME_S = 0.2


class HardwareSource:
    name = "hw"

    def __init__(self, cfg: Settings):
        self.cfg, self.hw = cfg, cfg.hardware
        self.lock = threading.Lock()
        self._ecg: list[float] = []
        self._lo: list[bool] = []
        self._imu: list[tuple[float, ...]] = []
        self._temp: list[float] = []
        self._grip: list[float] = []
        self._grip_active = False
        self._ecg_enabled = True
        self._lines: list[str] = []
        self._stop = threading.Event()
        self.ok = {"ecg": False, "imu": False, "temp": False, "gpio": False}
        self.errors: list[str] = []
        self.bus = self.mpu = self.ads = self.temp = self.lo_p = self.lo_m = None
        self._init_hw()
        self.link = SmallBandLink(self.hw.small_band_name, self.hw.ble_data_uuid,
                                  self.hw.ble_cmd_uuid, self._on_line)
        self.link.start()

    # ---- hardware init (each part optional) -------------------------------------------------
    def _init_hw(self) -> None:
        hw = self.hw
        from .drivers import ADS1115, MPU6050, TempSensor
        try:
            from smbus2 import SMBus  # type: ignore[import-not-found]
            self.bus = SMBus(hw.i2c_bus)
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"I2C: {e}")
        if self.bus:
            try:
                self.mpu = MPU6050(self.bus, hw.mpu_addr)
                self.mpu.calibrate_gyro()                 # keep the band still for ~1 s
                self.ok["imu"] = True
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"MPU6050: {e}")
            try:
                self.ads = ADS1115(self.bus, hw.ads_addr)
                self.ok["ecg"] = True
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"ADS1115: {e}")
        if hw.temp_sensor != "none":
            self.temp = TempSensor(hw.temp_sensor, self.bus, hw.mlx_addr, 0.0)
            self.ok["temp"] = True
        try:
            from gpiozero import DigitalInputDevice, DigitalOutputDevice  # type: ignore
            self.lo_p = DigitalInputDevice(hw.pin_lo_plus, pull_up=False)
            self.lo_m = DigitalInputDevice(hw.pin_lo_minus, pull_up=False)
            if hw.pin_ecg_sdn is not None:
                self._sdn = DigitalOutputDevice(hw.pin_ecg_sdn, initial_value=True)  # ECG on
            self.ok["gpio"] = True
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"GPIO (lead-off disabled): {e}")
        for target in (self._ecg_loop, self._imu_loop, self._temp_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()

    def _leads_off(self) -> bool:
        if self.lo_p is None or self.lo_m is None:
            return False
        return bool(self.lo_p.value or self.lo_m.value)

    # ---- sampler threads --------------------------------------------------------------------
    def _paced(self, hz: float):  # noqa: ANN202
        period, nxt = 1.0 / hz, time.perf_counter()
        while not self._stop.is_set():
            nxt += period
            yield
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.perf_counter()                 # fell behind: resync

    def _ecg_loop(self) -> None:
        if self.ads is None:
            return
        hw = self.hw
        for _ in self._paced(hw.ecg_hz):
            try:
                if self._grip_active:                      # grip test: the ADC channel is shared with ECG
                    g = self.ads.volts(hw.grip_channel)
                    with self.lock:
                        self._grip.append(g)
                    continue
                if not self._ecg_enabled:
                    continue
                v = self.ads.volts(hw.ecg_channel)
                mv = (v - hw.ecg_midpoint_v) / hw.ecg_gain * 1000.0
                lo = self._leads_off()
                with self.lock:
                    self._ecg.append(mv)
                    self._lo.append(lo)
            except OSError:
                time.sleep(0.1)

    def _imu_loop(self) -> None:
        if self.mpu is None:
            return
        filt, last = Madgwick(), time.perf_counter()
        for _ in self._paced(50):
            t = time.perf_counter()
            dt, last = t - last, t
            try:
                ax, ay, az, gx, gy, gz = self.mpu.read()
            except OSError:
                time.sleep(0.05)
                continue
            q = filt.update(ax, ay, az, gx, gy, gz, dt)
            with self.lock:
                self._imu.append((ax, ay, az, gx, gy, gz, *q))

    def _temp_loop(self) -> None:
        while not self._stop.is_set() and self.temp is not None:
            v = self.temp.read()
            if v is not None:
                with self.lock:
                    self._temp.append(v + self.hw.temp_offset_c)
            self._stop.wait(5.0)

    def _on_line(self, line: str) -> None:
        with self.lock:
            self._lines.append(line)

    # ---- source interface -------------------------------------------------------------------
    async def stream(self) -> AsyncIterator[Bundle]:
        t0 = time.monotonic()
        prev = 0.0
        while True:
            await asyncio.sleep(FRAME_S)
            now = time.monotonic() - t0
            with self.lock:
                ecg, lo, imu, grip = self._ecg, self._lo, self._imu, self._grip
                temps, lines = self._temp, self._lines
                self._ecg, self._lo, self._imu, self._temp, self._lines = [], [], [], [], []
                self._grip = []
            n = len(imu)
            arr = np.asarray(imu, dtype=float).reshape(n, 10)
            chunk = ImuChunk(50, prev, arr[:, 0:3], arr[:, 3:6], arr[:, 6:10])
            # the main band is the waist IMU in monitoring and the reference limb in rehab
            frame = SensorFrame(
                t0=prev, t1=now,
                ecg=EcgChunk(self.hw.ecg_hz, prev, np.asarray(ecg, dtype=float),
                             np.asarray(lo, dtype=bool)),
                imu={"waist": chunk, "limb_ref": chunk, "limb_moving": ImuChunk(
                    50, prev, np.zeros((n, 3)), np.zeros((n, 3)), np.tile([1.0, 0, 0, 0], (n, 1)))},
                ppg=PpgChunk(25, prev, np.zeros(0, np.int32), np.zeros(0, np.int32),
                             np.zeros(0, bool)),
                temp=[TempSample(now, v) for v in temps],
                grip=GripChunk(self.hw.ecg_hz, prev, np.asarray(grip, dtype=float)) if grip else None)
            prev = now
            yield Bundle(frame, lines)

    def send_band_command(self, text: str) -> None:
        self.link.send_command(text)

    def set_grip(self, active: bool) -> None:
        self._grip_active = active

    def set_ecg(self, active: bool) -> None:
        """AD8232 SDN pin: high = ECG on. Samples stop while it is off (the sampler thread idles)."""
        self._ecg_enabled = active
        sdn = getattr(self, "_sdn", None)
        if sdn is not None:
            sdn.on() if active else sdn.off()

    def health(self) -> dict:
        return {"source": "hw", **self.ok, "ble": self.link.connected,
                "ble_error": self.link.error, "errors": self.errors}

    def close(self) -> None:
        self._stop.set()

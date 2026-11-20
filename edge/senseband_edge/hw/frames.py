"""Sensor data types shared by the simulator and the real drivers (so the pipeline never
cares where samples come from).

Units: ECG in millivolts (before the AD8232 gain), IMU accel in g, gyro in deg/s,
quaternions as (w, x, y, z), PPG as raw 18-bit MAX30102 counts, temperature in deg C,
grip as volts at the ADS1115 input.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class EcgChunk:
    fs: int
    t0: float
    mv: np.ndarray            # (n,)
    lead_off: np.ndarray      # (n,) bool, True = electrode off (LO+ or LO- high)


@dataclass(frozen=True)
class ImuChunk:
    fs: int
    t0: float
    acc_g: np.ndarray         # (n, 3)
    gyro_dps: np.ndarray      # (n, 3)
    quat_wxyz: np.ndarray     # (n, 4)


@dataclass(frozen=True)
class PpgChunk:
    fs: int
    t0: float
    red: np.ndarray           # (n,) raw counts
    ir: np.ndarray            # (n,) raw counts
    finger: np.ndarray        # (n,) bool, same rule as the firmware: ir > 50000


@dataclass(frozen=True)
class TempSample:
    t: float
    celsius: float


@dataclass(frozen=True)
class GripChunk:
    fs: int
    t0: float
    volts: np.ndarray         # (n,)


@dataclass(frozen=True)
class Truth:
    """Ground truth at the end of a frame. Used by tests and to label ML training data."""

    t: float
    segment: int
    label: str
    activity: str
    posture: str
    hr: float                 # target heart rate (bpm); 0 = no heartbeat
    rhythm: str               # "normal" | "af"
    spo2: float               # includes desaturation dips
    temp_c: float
    motion: float             # 0..1
    lead_off: bool
    finger_off: bool
    ecg_noise: float
    joint_angle_deg: float | None = None
    event: str | None = None  # "fall" | "collapse" if one started inside this frame


@dataclass(frozen=True)
class SensorFrame:
    t0: float
    t1: float
    ecg: EcgChunk
    imu: dict[str, ImuChunk]  # keys: "waist", "limb_ref", "limb_moving"
    ppg: PpgChunk
    temp: list[TempSample] = field(default_factory=list)
    grip: GripChunk | None = None
    truth: Truth | None = None

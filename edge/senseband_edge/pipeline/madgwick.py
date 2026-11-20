"""Madgwick IMU orientation filter (gyro + accelerometer -> quaternion, w x y z).

Same algorithm as firmware/small_band/small_band.ino so both bands report orientation the same
way. Yaw is unobservable without a magnetometer: joint angles use the RELATIVE quaternion of the
two bands about the hinge (Milestone 3), where the common yaw cancels.
"""
from __future__ import annotations

import math


class Madgwick:
    def __init__(self, beta: float = 0.1):
        self.beta = beta
        self.q = (1.0, 0.0, 0.0, 0.0)

    def update(self, ax: float, ay: float, az: float, gx_dps: float, gy_dps: float,
               gz_dps: float, dt: float) -> tuple[float, float, float, float]:
        q0, q1, q2, q3 = self.q
        gx, gy, gz = (math.radians(v) for v in (gx_dps, gy_dps, gz_dps))
        qd0 = 0.5 * (-q1 * gx - q2 * gy - q3 * gz)
        qd1 = 0.5 * (q0 * gx + q2 * gz - q3 * gy)
        qd2 = 0.5 * (q0 * gy - q1 * gz + q3 * gx)
        qd3 = 0.5 * (q0 * gz + q1 * gy - q2 * gx)
        n = math.sqrt(ax * ax + ay * ay + az * az)
        if n > 1e-6:
            ax, ay, az = ax / n, ay / n, az / n
            f1 = 2 * (q1 * q3 - q0 * q2) - ax
            f2 = 2 * (q0 * q1 + q2 * q3) - ay
            f3 = 2 * (0.5 - q1 * q1 - q2 * q2) - az
            s0 = -2 * q2 * f1 + 2 * q1 * f2
            s1 = 2 * q3 * f1 + 2 * q0 * f2 - 4 * q1 * f3
            s2 = -2 * q0 * f1 + 2 * q3 * f2 - 4 * q2 * f3
            s3 = 2 * q1 * f1 + 2 * q2 * f2
            sn = math.sqrt(s0 * s0 + s1 * s1 + s2 * s2 + s3 * s3)
            if sn > 0:
                qd0 -= self.beta * s0 / sn
                qd1 -= self.beta * s1 / sn
                qd2 -= self.beta * s2 / sn
                qd3 -= self.beta * s3 / sn
        q0, q1, q2, q3 = q0 + qd0 * dt, q1 + qd1 * dt, q2 + qd2 * dt, q3 + qd3 * dt
        n = math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
        self.q = (q0 / n, q1 / n, q2 / n, q3 / n)
        return self.q

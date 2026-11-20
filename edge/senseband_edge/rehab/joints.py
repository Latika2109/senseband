"""Joint movements and the relative-quaternion angle (docs/03 wearing table, docs/04 M7).

Convention (both bands strapped with the same side facing out, IMU axes aligned at the neutral pose):
  x = abduction / deviation axis, y = flexion hinge, z = rotation about the limb.
`sign` flips the direction so that the named movement is positive. The common yaw of the two bands
cancels in the relative rotation, which is why no magnetometer is needed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation as R

AXES = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}


@dataclass(frozen=True)
class Movement:
    key: str
    joint: str
    axis: str
    sign: int


MOVEMENTS: dict[str, Movement] = {m.key: m for m in (
    Movement("shoulder_flexion", "shoulder", "y", 1), Movement("shoulder_abduction", "shoulder", "x", 1),
    Movement("shoulder_rotation", "shoulder", "z", 1),
    Movement("elbow_flexion", "elbow", "y", 1), Movement("forearm_rotation", "elbow", "z", 1),
    Movement("wrist_flexion", "wrist", "y", 1), Movement("wrist_extension", "wrist", "y", -1),
    Movement("wrist_deviation", "wrist", "x", 1),
    Movement("hip_flexion", "hip", "y", 1), Movement("hip_abduction", "hip", "x", 1),
    Movement("knee_flexion", "knee", "y", -1),
    Movement("ankle_dorsiflexion", "ankle", "y", 1), Movement("ankle_plantarflexion", "ankle", "y", -1),
)}
JOINTS = ("shoulder", "elbow", "wrist", "hip", "knee", "ankle")


def movements_of(joint: str) -> list[str]:
    return [k for k, m in MOVEMENTS.items() if m.joint == joint]


def to_rot(wxyz: tuple[float, float, float, float] | np.ndarray) -> R:
    w, x, y, z = wxyz
    return R.from_quat([x, y, z, w])


def relative(q_ref: tuple, q_mov: tuple) -> R:
    """Orientation of the moving segment expressed in the reference segment's frame."""
    return to_rot(q_ref).inv() * to_rot(q_mov)


class JointAngle:
    """Calibrated angle between the reference band and the moving band."""

    def __init__(self, neutral: R | None = None):
        self.neutral = neutral if neutral is not None else R.identity()

    def delta(self, q_ref: tuple, q_mov: tuple) -> np.ndarray:
        """Rotation vector (deg, in the moving frame) away from the calibrated neutral pose."""
        return (self.neutral.inv() * relative(q_ref, q_mov)).as_rotvec(degrees=True)

    def angle(self, q_ref: tuple, q_mov: tuple, movement: str) -> tuple[float, float]:
        """(signed angle about the movement axis, off-axis error in deg). Positive = the movement."""
        m = MOVEMENTS[movement]
        rv = self.delta(q_ref, q_mov)
        on = float(rv @ AXES[m.axis]) * m.sign
        off = float(np.linalg.norm(rv - (rv @ AXES[m.axis]) * AXES[m.axis]))
        return on, off

    @staticmethod
    def calibrate(pairs: list[tuple[tuple, tuple]], steady_deg: float) -> tuple[R, float]:
        """Average relative rotation over the still pose. Returns (neutral, wobble_deg)."""
        rels = R.concatenate([relative(a, b) for a, b in pairs])
        mean = rels.mean()
        wobble = float(np.max((mean.inv() * rels).magnitude() * 180.0 / np.pi))
        if wobble > steady_deg:
            raise ValueError(f"not steady: moved {wobble:.1f} deg")
        return mean, wobble

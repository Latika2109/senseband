"""Shared feature extraction used by BOTH training (ml/train) and the edge service (edge/senseband_edge).

One implementation = no train/serve skew (CLAUDE.md). Pure numpy/scipy, no sklearn, so the Pi can import it.
Feature ORDER is part of the contract: models are trained against `*_FEATURES` and the edge feeds them in the
same order. Bump FEATURE_VERSION on any change (the model metrics file records it; the edge refuses a mismatch).
"""
from .common import FEATURE_VERSION
from .ecg import ECG_QUALITY_FEATURES, RR_FEATURES, ecg_quality_features, r_peaks, rr_features, rr_intervals
from .fall import FALL_FEATURES, fall_window_features
from .hr import ACTIVITY_CODES, EXPECTED_HR_FEATURES, expected_hr_features
from .imu import IMU_FEATURES, imu_window_features

__all__ = [
    "ACTIVITY_CODES", "ECG_QUALITY_FEATURES", "EXPECTED_HR_FEATURES", "FALL_FEATURES", "FEATURE_VERSION",
    "IMU_FEATURES", "RR_FEATURES", "ecg_quality_features", "expected_hr_features", "fall_window_features",
    "imu_window_features", "r_peaks", "rr_features", "rr_intervals",
]

"""Edge settings: edge/config/default.yaml, deep-merged with SENSEBAND_CONFIG (YAML) if set."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "default.yaml"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Patient(_M):
    id: str
    name: str
    age: int
    beta_blocker: bool
    resting_hr: float | None


class Sim800(_M):
    port: str
    baud: int


class TwilioCfg(_M):
    api_base: str
    timeout_s: float


class Sms(_M):
    contacts: list[str]
    retries: int
    retry_delay_s: float
    sim800: Sim800
    provider: str              # auto | twilio | sim800 | mock
    twilio: TwilioCfg


class BandsCfg(_M):
    enabled: bool
    primary: str
    waist_imu_from: str
    max_devices: int
    max_body_bytes: int
    stale_s: float


class Alerts(_M):
    countdown_s: float
    sos_countdown_s: float
    cooldown_s: dict[str, float]
    ecg_strip_s: float
    buzzer_pattern_s: list[float]


class Thresholds(_M):
    hr_low: float
    hr_low_sleep: float
    hr_low_sustain_s: float
    hr_high_rest: float
    hr_critical_rest: float
    hr_rest_sustain_s: float
    hr_critical_sustain_s: float
    hr_high_active: float
    hr_active_sustain_s: float
    recovery_allowance_s: float
    spo2_warn: float
    spo2_warn_sustain_s: float
    spo2_crit: float
    spo2_crit_sustain_s: float
    desat_drop_pct: float
    desat_events_warn: int
    desat_window_s: float
    temp_high: float
    temp_hr_margin: float
    temp_sustain_s: float
    rr_irregularity: float
    rr_irregular_sustain_s: float
    no_pulse_s: float
    check_sensor_s: float


class Quality(_M):
    ecg_flat_mv: float
    ecg_noise_hf_mv: float
    ecg_min_qrs_mv: float
    ecg_qrs_frac: float
    ppg_hr_range: list[float]
    ppg_spo2_range: list[float]
    motion_ppg_invalid_g: float


class ActivityCfg(_M):
    window_s: float
    hop_s: float
    rest_std_g: float
    run_std_g: float
    lying_axis_g: float
    sitting_tilt_deg: float
    sleep_after_lying_s: float
    cadence_run_hz: float


class ExpectedHr(_M):
    lying: list[float]
    sleeping: list[float]
    sitting: list[float]
    standing: list[float]
    walking: list[float]
    stairs: list[float]
    exercising: list[float]
    running: list[float]
    beta_blocker_scale: float


class Ews(_M):
    hr: list[list[float]]
    spo2: list[list[float]]
    temp: list[list[float]]
    watch_at: int
    alert_at: int


class Fall(_M):
    freefall_g: float
    impact_g: float
    hard_impact_g: float
    still_s: float
    still_after_s: float
    moving_dev_g: float


class Storage(_M):
    vitals_minute_s: float
    ecg_ring_s: float
    keep_raw: bool
    raw_days: int
    recordings_dir: str
    max_recording_s: float


class Hardware(_M):
    i2c_bus: int
    mpu_addr: int
    ads_addr: int
    ecg_channel: int
    grip_channel: int
    ecg_hz: int
    ecg_gain: float
    ecg_midpoint_v: float
    pin_lo_plus: int
    pin_lo_minus: int
    pin_ecg_sdn: int | None
    pin_button: int
    pin_buzzer: int
    pin_led: int
    sos_hold_s: float
    temp_sensor: str
    mlx_addr: int
    temp_offset_c: float
    grip_cal: list[list[float]]
    small_band_name: str
    ble_data_uuid: str
    ble_cmd_uuid: str


class Rehab(_M):
    calibration_s: float
    calibration_steady_deg: float
    calibration_max_age_h: float
    rep_up_frac: float
    rep_down_frac: float
    default_target_deg: float
    in_target_frac: float
    too_fast_factor: float
    max_speed_dps: float
    pain_stop: int
    angle_msg_every: int
    band_rate_exercise_hz: int
    band_rate_idle_hz: int
    haptic_ms: int
    grip_test_s: float
    grip_reference_kg: float
    stage_bounds_pct: list[float]
    forecast_min_sessions: int
    forecast_bootstrap: int
    forecast_target_pct: float
    sessions_per_day: float
    pain_gentle_at: int
    low_adherence: float
    normal_rom: dict[str, float]


class Cloud(_M):
    enabled: bool
    url: str
    device_key: str
    batch_rows: int
    batch_bytes: int
    timeout_s: float
    backoff_s: list[float]
    pull_every_s: float
    dead_after_attempts: int
    priority: list[str]


class _Toggle(_M):
    enabled: bool


class ActivityModelCfg(_Toggle):
    min_prob: float


class FallModelCfg(_Toggle):
    veto_below: float


class QualityModelCfg(_Toggle):
    min_prob: float


class RhythmModelCfg(_Toggle):
    af_prob: float
    min_beats: int


class ExpectedHrCfg(_Toggle):
    min_band_bpm: float
    learning_band_scale: float


class ModelsCfg(_M):
    enabled: bool
    dir: str
    max_infer_ms: float
    learning_period_h: float
    activity: ActivityModelCfg
    fall: FallModelCfg
    ecg_quality: QualityModelCfg
    rhythm: RhythmModelCfg
    expected_hr: ExpectedHrCfg


class LoggingCfg(_M):
    dir: str
    level: str
    max_mb: float
    backups: int


class PowerCfg(_M):
    ecg_duty_cycle: bool
    sleep_min_s: float
    ppg_stable_s: float
    ppg_stable_band_bpm: float
    wake_every_s: float
    wake_for_s: float
    resume_if_ppg_bad_s: float


class OtaCfg(_M):
    enabled: bool
    manifest_url: str
    public_key: str
    root: str
    keep: int
    health_timeout_s: float
    max_bytes: int
    restart_cmd: list[str]


class Settings(_M):
    mode: str
    db_path: str
    port: int
    device_name: str
    cors_origins: list[str]
    patient: Patient
    sms: Sms
    alerts: Alerts
    thresholds: Thresholds
    quality: Quality
    activity: ActivityCfg
    expected_hr: ExpectedHr
    ews: Ews
    fall: Fall
    storage: Storage
    hardware: Hardware
    rehab: Rehab
    cloud: Cloud
    models: ModelsCfg
    bands: BandsCfg
    logging: LoggingCfg
    power: PowerCfg
    ota: OtaCfg

    @property
    def is_sim(self) -> bool:
        if self.mode == "auto":
            return os.environ.get("SENSEBAND_SIM") == "1"
        return self.mode == "sim"


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_settings(overrides: dict[str, Any] | None = None, path: str | None = None) -> Settings:
    with open(DEFAULT_PATH) as f:
        data = yaml.safe_load(f)
    extra = path or os.environ.get("SENSEBAND_CONFIG")
    if extra:
        with open(extra) as f:
            data = _merge(data, yaml.safe_load(f) or {})
    if overrides:
        data = _merge(data, overrides)
    settings = Settings.model_validate(data)
    nums = os.environ.get("ALERT_TO_NUMBERS")
    if nums:                                              # secrets/recipients from the environment, never from git
        from .alerts.twilio import is_e164, parse_numbers
        good = [n for n in parse_numbers(nums) if is_e164(n)]
        if good:
            settings.sms.contacts = good
    return settings

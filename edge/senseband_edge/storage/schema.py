"""SQLite schema (edge side of docs/05_DATA_MODEL_AND_API.md) and versioned migrations.

Rules: ids are UUIDv4 created on the edge; timestamps are UTC epoch ms; list/dict columns are JSON
text. Add a NEW entry to MIGRATIONS for every schema change - never edit an old one.
"""
from __future__ import annotations

# table -> (primary key columns, JSON columns). Used by Store for upserts/outbox.
TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "patients": (("id",), ("conditions", "medications", "emergency_contacts")),
    "devices": (("id",), ("fw_versions",)),
    "programs": (("id",), ("settings_json",)),
    "recovery_cases": (("id",), ("max_angle_limits_json",)),
    "calibrations": (("id",), ("offsets_json",)),
    "vitals_minute": (("patient_id", "minute_ts"), ("quality_flags",)),
    "events": (("id",), ("context_json",)),
    "ecg_strips": (("id",), ()),
    "assessments": (("id",), ()),
    "exercise_plans": (("id",), ("items_json",)),
    "exercise_sessions": (("id",), ()),
    "forecasts": (("id",), ("params_json",)),
    "notes": (("id",), ()),
}

MIGRATIONS: list[str] = [
    # ---- 1: initial schema ------------------------------------------------------------------
    """
    CREATE TABLE patients(id TEXT PRIMARY KEY, name TEXT, dob TEXT, sex TEXT, height_cm REAL,
        weight_kg REAL, conditions TEXT, medications TEXT, resting_hr REAL,
        emergency_contacts TEXT, created_at INTEGER);
    CREATE TABLE devices(id TEXT PRIMARY KEY, patient_id TEXT, main_serial TEXT,
        small_ble_name TEXT, fw_versions TEXT, last_seen INTEGER);
    CREATE TABLE programs(id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, type TEXT NOT NULL,
        purpose TEXT, start_ts INTEGER NOT NULL, planned_end_ts INTEGER, status TEXT NOT NULL,
        settings_json TEXT);
    CREATE TABLE recovery_cases(id TEXT PRIMARY KEY, program_id TEXT NOT NULL, limb TEXT,
        joint TEXT, side TEXT, injury_type TEXT, injury_date TEXT, cast_removed_date TEXT,
        max_angle_limits_json TEXT, weight_bearing TEXT, doctor_approved INTEGER DEFAULT 0);
    CREATE TABLE calibrations(id TEXT PRIMARY KEY, program_id TEXT, ts INTEGER, joint TEXT,
        offsets_json TEXT);
    CREATE TABLE vitals_minute(patient_id TEXT NOT NULL, minute_ts INTEGER NOT NULL,
        hr_ecg REAL, hr_ppg REAL, hr_expected REAL, spo2_min REAL, spo2_avg REAL, temp REAL,
        activity TEXT, intensity REAL, posture TEXT, rhythm TEXT, quality_flags TEXT, ews INTEGER,
        PRIMARY KEY(patient_id, minute_ts));
    CREATE TABLE events(id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, ts INTEGER NOT NULL,
        level TEXT NOT NULL, kind TEXT NOT NULL, message TEXT, context_json TEXT,
        acknowledged_by TEXT, ack_ts INTEGER, sms_sent INTEGER DEFAULT 0, status TEXT,
        cancelled_ts INTEGER);
    CREATE INDEX events_ts ON events(patient_id, ts);
    CREATE TABLE ecg_strips(id TEXT PRIMARY KEY, event_id TEXT, ts INTEGER, fs INTEGER,
        samples BLOB);
    CREATE TABLE assessments(id TEXT PRIMARY KEY, program_id TEXT, ts INTEGER, kind TEXT,
        movement TEXT, side TEXT, min_angle REAL, max_angle REAL, rom REAL, grip_peak REAL,
        grip_mean REAL, unit TEXT, pain_0_10 INTEGER, phase TEXT);
    CREATE TABLE exercise_plans(id TEXT PRIMARY KEY, program_id TEXT, version INTEGER,
        created_by TEXT, approved_by TEXT, stage TEXT, items_json TEXT);
    CREATE TABLE exercise_sessions(id TEXT PRIMARY KEY, program_id TEXT, plan_id TEXT,
        start_ts INTEGER, end_ts INTEGER, exercise_id TEXT, reps_done INTEGER,
        avg_peak_angle REAL, max_angle REAL, target_hit_pct REAL, too_fast_count INTEGER,
        pain_before INTEGER, pain_after INTEGER, stopped_reason TEXT);
    CREATE TABLE forecasts(id TEXT PRIMARY KEY, program_id TEXT, ts INTEGER, params_json TEXT,
        est_target_date TEXT, ci_low TEXT, ci_high TEXT);
    CREATE TABLE notes(id TEXT PRIMARY KEY, patient_id TEXT, author_id TEXT, ts INTEGER,
        text TEXT);
    CREATE TABLE outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, "table" TEXT NOT NULL,
        row_id TEXT NOT NULL, op TEXT NOT NULL, payload TEXT NOT NULL, created_ts INTEGER NOT NULL,
        sent_ts INTEGER);
    CREATE INDEX outbox_pending ON outbox(sent_ts) WHERE sent_ts IS NULL;
    CREATE TABLE raw_chunks(ts INTEGER NOT NULL, stream TEXT NOT NULL, payload BLOB);
    CREATE INDEX raw_chunks_ts ON raw_chunks(ts);
    """,
    # ---- 2: sync state (Milestone 4) ---------------------------------------------------------
    """
    ALTER TABLE outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE outbox ADD COLUMN last_error TEXT;
    ALTER TABLE notes ADD COLUMN author_name TEXT;
    CREATE TABLE sync_state(key TEXT PRIMARY KEY, value TEXT);
    """,
]

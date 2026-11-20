# 05 – Data model and API

All ids are UUIDv4 created where the row is born. Timestamps are UTC epoch ms. Same tables exist in
SQLite (edge) and Postgres (cloud) unless marked.

## Tables
| Table | Key columns |
|---|---|
| users *(cloud)* | id, name, phone, email, role_default |
| patients | id, name, dob, sex, height_cm, weight_kg, conditions[], medications[], resting_hr, emergency_contacts[] (name, phone, relation), created_at |
| care_team *(cloud)* | patient_id, user_id, role (patient/caregiver/doctor), status (invited/active/revoked) |
| devices | id, patient_id, main_serial, small_ble_name, fw_versions, last_seen |
| programs | id, patient_id, type (monitoring/recovery), purpose, start_ts, planned_end_ts (null = continuous), status, settings_json |
| recovery_cases | id, program_id, limb, joint, side, injury_type, injury_date, cast_removed_date, max_angle_limits_json, weight_bearing, doctor_approved |
| calibrations | id, program_id, ts, joint, offsets_json (quaternions) |
| vitals_minute | patient_id, minute_ts, hr_ecg, hr_ppg, hr_expected, spo2_min, spo2_avg, temp, activity, intensity, posture, rhythm, quality_flags, ews |
| events | id, patient_id, ts, level, kind, message, context_json, acknowledged_by, ack_ts, sms_sent |
| ecg_strips | id, event_id, ts, fs, samples (blob, edge) / storage_path (cloud) |
| assessments | id, program_id, ts, kind (rom/grip/pain), movement, side, min_angle, max_angle, rom, grip_peak, grip_mean, unit, pain_0_10, phase (pre/post/baseline) |
| exercise_plans | id, program_id, version, created_by (system/doctor), approved_by, stage, items_json [{exercise_id, sets, reps, target_angle, hold_s}] |
| exercise_sessions | id, program_id, plan_id, start_ts, end_ts, exercise_id, reps_done, avg_peak_angle, max_angle, target_hit_pct, too_fast_count, pain_before, pain_after, stopped_reason |
| forecasts | id, program_id, ts, params_json, est_target_date, ci_low, ci_high |
| notes | id, patient_id, author_id, ts, text |
| outbox *(edge)* | id, table, row_id, op, payload, created_ts, sent_ts |
| raw_chunks *(edge, 7-day rolling)* | ts, stream, payload |

Supabase RLS: a user can read a patient's rows only with an active care_team row; only doctors can
write exercise_plans (approved) and notes; devices can insert only their own patient's rows.

## Edge REST API (FastAPI, `http://<band>:8080/api`)
| Method | Path | Purpose |
|---|---|---|
| GET | /status | sensors, BLE, battery, SIM, internet, sync backlog, active program |
| GET/PUT | /patient | local profile |
| POST | /programs | start program {type, purpose, duration_h or null, recovery_case?} |
| POST | /programs/{id}/stop | end program |
| GET | /signal-check | live quality of each sensor with fix tips |
| POST | /calibrate | {joint} – capture neutral pose |
| POST | /assessments/rom/start · /stop | {movement, phase} → result |
| POST | /assessments/grip | {side} → 5 s test result |
| POST | /assessments/pain | {score, phase} |
| GET | /plan · POST /plan/approve | current plan |
| POST | /exercise/start · /stop | {exercise_id} → session summary |
| GET | /history?from&to&kind | vitals_minute, events, assessments, sessions |
| GET | /report?program_id&format=pdf | report |
| POST | /events/{id}/ack | acknowledge |
| POST | /record/start · /stop | training-data recording {label} |
| POST | /sync/now | force sync |

## WebSocket `/ws/live` (server → app, JSON)
```json
{"t":"vitals","ts":0,"hr":72,"hr_src":"ecg","hr_expected":75,"context":"sitting","spo2":97,
 "temp":36.4,"rhythm":"regular","quality":{"ecg":"good","ppg":"good"},"ews":0}          // 1 Hz
{"t":"ecg","ts":0,"fs":250,"samples":[...]}                                               // 5 Hz chunks
{"t":"angle","ts":0,"joint":"knee","angle":42.5,"rep":3,"in_target":true,"too_fast":false} // 25 Hz in exercise
{"t":"event","level":"critical","kind":"fall","message":"...","id":"..."}
{"t":"status", ...}
```

## BLE protocol (small band → main band)
Service `5e75e000-0000-4000-8000-00000000b001`, notify char `...b002`, command char `...b003` (write).
Upgrade text lines to: `Q,ms,w,x,y,z` (quaternion, 50 Hz in exercise / 10 Hz otherwise),
`V,hr,hrValid,spo2,spo2Valid,finger,perfusion`, `B,battery_pct`.
Commands: `RATE,10|25|50`, `VIB,ms`, `CAL` (gyro recalibration).

---
## Additions made while building Milestones 1–6 (the code is the source of truth: `edge/senseband_edge/api/app.py`)

### Edge REST (all under `/api`)
| Method | Path | Notes |
|---|---|---|
| GET | /pair | `{name, api:1, port, mode, patient_id, urls[], qr}`; the QR on the band encodes `senseband://<ip>:<port>`; CORS is enabled for the app |
| POST | /imok · /sos | cancel the 30 s SMS countdown (same as the short button press) · immediate SOS SMS (long press) |
| GET | /events/{id}/strip | ECG strip saved with an alert (mV, decimated) |
| GET | /notes | doctor notes pulled from the cloud |
| POST | /sync/now · POST/DELETE /cloud/link | force a sync · store/remove the cloud URL + device key (survives restarts) |
| GET | /rehab/state · /exercises?joint= · /recovery · /progress | rehab engine state, exercise library, recovery case, progress + forecast |
| POST | /calibrate · /assessments/rom/start · /stop · /assessments/grip · /assessments/pain | `409 {"code": "not_calibrated"\|"busy"\|"not_running"\|"pain_too_high"}` on misuse |
| POST | /plan/generate · /plan/approve · PUT /recovery/limits | plans are defaults until a doctor approves; limits are the doctor's maximum angles |
| POST | /exercise/start · /stop · /pain | pain ≥ 7 stops a session automatically |
| POST | /record/start · /stop · GET /record | "Record training data" mode: labelled `.npz` files for `ml/` (label `[a-z0-9_-]{1,40}`) |
| GET | /models | which ONNX models are active and why the others are not |
| GET | /report?program_id=&format=pdf\|json&hours=&day= | PDF health report (English) or its JSON |
| GET | /export · POST /data/erase `{"confirm":"DELETE"}` | DPDP Act 2023: all data as a ZIP of plain files · permanent erase + `VACUUM` |

`POST /programs` with `type: "recovery"` needs `recovery_case: {joint, side, injury_date, injury_type?, max_angle_limits?}`.

### WebSocket `/ws/live` – messages added
`vitals` now also carries `posture, intensity, hr_range [lo,hi], learning, status ("normal"|"watch"|"alert")` and quality values
`ecg: collecting|good|noisy|lead_off|flat|paused`. `event` messages carry `why` (raised, cancelled, sms_due, sms_done, acknowledged),
`raised_t` (alert time on the band clock, never `t`, which is the message type) and `countdown_left_s`. Exercise mode adds
`angle` (25 Hz), `rep`, `limit_exceeded` and `exercise_done`.

### Small-band BLE protocol v2
`Q,ms,w,x,y,z` · `V,hr,hrValid,spo2,spo2Valid,finger,perfusion` · `B,pct`; commands `RATE,10|25|50`, `VIB,ms`, `CAL`.
Firmware: `firmware/small_band/small_band.ino` (written, **not compiled or flashed yet**).

### Edge storage changes
Migration 2 adds `outbox.attempts/last_error`, `notes.author_name` and the `sync_state` key/value table (pull cursor,
cloud link, patient id, personal-HR state). The patient id is generated once per band (`p-<12 hex>`) and is the cloud key.

### Cloud (`cloud/supabase/migrations`)
Tables mirror the edge schema (ids are text), plus `profiles, care_team, invites, devices(key_hash), push_subscriptions`.
RPCs: `create_patient, create_invite, accept_invite, revoke_access, ack_event, register_device, delete_patient_data`
(signed-in users) and `ingest_batch, pull_changes` (a band, authenticated by its device key). Edge Functions: `ingest`, `pull`,
`notify` (`cloud/README.md`). Cloud-authoritative rows (profile, plans, limits, notes) carry `cloud_edited`; measurements are
edge-authoritative and upserted idempotently.

# Report fill-ins (drafted from the SenseBand code, 2026-11-20)

Each blank is marked **FILLED** (written from the code or docs in this repository), **PARTIAL** (part is code, part needs you) or
**YOU** (only you know it: hardware you built, tests you ran, prices you paid). Nothing below is a measured result.

## 4.2.2 Usage modes - FILLED
- **Small band (ESP32) on its own:** it measures and does some on-board processing: heart rate and SpO2 from the MAX30102 (Maxim algorithm,
  window recalculated every 1 s), a Madgwick orientation quaternion from the MPU6050, a perfusion index and the battery level. It **cannot
  analyse context or raise alerts on its own**: it has no alert logic, no storage and no SMS. It sends its readings by Bluetooth LE
  (`Q`, `V`, `B` text lines) to the main band.
- **Main band (Raspberry Pi) alone:** full operation. It records ECG, waist motion and skin temperature, runs the activity/rhythm/fall
  rules (and the ML models if present), stores everything in SQLite, raises alerts, sounds the buzzer, and sends SMS through the SIM800L, with no
  phone and no internet. Without the small band it keeps working but has no SpO2 and no second joint sensor (so no joint-angle tracking).
- **Phone app:** optional for safety. It connects to the main band over local Wi-Fi (Local mode) to show live data and guide exercises,
  or to the cloud (Cloud mode) for history, family and doctors. If the phone is absent, alerts still reach contacts by SMS.
- **Cloud:** optional. The band syncs when internet exists; nothing depends on it.

## 4.2.4 Database design - FILLED (cloud service is Supabase, not Firebase)
**Edge (SQLite, WAL, on the main band)** - ids are UUIDs created on the band; times are UTC epoch milliseconds.
| Table | Main fields |
|---|---|
| patients | id, name, dob, sex, height_cm, weight_kg, conditions, medications, resting_hr, emergency_contacts, created_at |
| devices | id, patient_id, main_serial, small_ble_name, fw_versions, last_seen |
| programs | id, patient_id, type (monitoring/recovery), purpose, start_ts, planned_end_ts, status, settings_json |
| recovery_cases | id, program_id, limb, joint, side, injury_type, injury_date, cast_removed_date, max_angle_limits_json, weight_bearing, doctor_approved |
| calibrations | id, program_id, ts, joint, offsets_json |
| vitals_minute | patient_id, minute_ts, hr_ecg, hr_ppg, hr_expected, spo2_min, spo2_avg, temp, activity, intensity, posture, rhythm, quality_flags, ews |
| events | id, patient_id, ts, level, kind, message, context_json, acknowledged_by, ack_ts, sms_sent, status, cancelled_ts |
| ecg_strips | id, event_id, ts, fs, samples |
| assessments | id, program_id, ts, kind (rom/grip/pain), movement, side, min_angle, max_angle, rom, grip_peak, grip_mean, unit, pain_0_10, phase |
| exercise_plans | id, program_id, version, created_by, approved_by, stage, items_json |
| exercise_sessions | id, program_id, plan_id, start_ts, end_ts, exercise_id, reps_done, avg_peak_angle, max_angle, target_hit_pct, too_fast_count, pain_before, pain_after, stopped_reason |
| forecasts, notes | recovery-date estimates; doctor notes |
| outbox | id, table, row_id, op, payload, created_ts, sent_ts, attempts, last_error (every write is queued here for sync) |
| raw_chunks, sync_state | optional 7-day raw ECG/IMU store; key/value state (pull cursor, cloud link, patient id) |

**Cloud: Supabase** (PostgreSQL + Auth with phone OTP + Row Level Security + Realtime + Storage + Edge Functions). It has the same tables
plus `profiles`, `care_team` (patient, user, role patient/caregiver/doctor, status), `invites`, `push_subscriptions`. Doctors and family only
see patients that invited them; a band writes only its own patient's rows using a secret device key. Source: `cloud/supabase/migrations/`.

## 4.2.5 Input design - PARTIAL
Input screens that exist in the app (take screenshots from `make sim` + `make app`): Welcome, Patient profile (name, age, sex, height,
weight, conditions, medicines, beta-blocker checkbox, resting HR, emergency contacts), Pair device (auto-find, scan QR, type address), Choose
program, Purpose, Duration, Wear guide (checklist), Signal check, Injury location, Injury details (dates, doctor's maximum angle, pain 0-10),
Calibrate, ROM/grip tests and pain scale, Exercise pre-check (pain), Sign-in (phone OTP), Invite code, Doctor plan editor and notes.
**YOU:** insert the screenshots.

## 4.2.6 Output design - FILLED (screens) / screenshots YOU
Output screens: Live dashboard (heart rate with "normal/higher/lower than usual for <activity>", SpO2, temperature, rhythm, activity, ECG trace,
HR-vs-expected chart, status All normal/Watch/Alert), Alerts list with ECG strip, "Are you OK?" countdown banner, Guided exercise (angle gauge,
rep/set counter, tempo and "too fast" warnings), Progress (range-of-movement chart, grip, adherence, recovery estimate), PDF report, doctor
patient list sorted by severity.

**Exact alert wording (from `engine.py`; none diagnoses):**
| Level | Kind | Text |
|---|---|---|
| Critical | fall | "Possible fall - no movement detected. Press the button if OK." |
| Critical | collapse | "No heartbeat detected and no movement - possible collapse. Press the button if OK." |
| Critical | hr_critical_rest | "Heart rate very high (N bpm) at rest. Press the button if OK." |
| Critical | hr_low | "Heart rate very low (N bpm). Press the button if OK." |
| Critical | spo2_critical | "Blood oxygen very low (N %). Press the button if OK." |
| Critical | sos | "SOS button pressed - needs help now." |
| Warning | hr_high_rest | "Heart rate N bpm while resting - higher than expected. Rest and recheck; contact your doctor if it continues." |
| Warning | hr_high_active | "Heart rate N bpm during activity - consider slowing down." |
| Warning | spo2_low | "Blood oxygen N % - lower than expected. Sit upright, breathe slowly and recheck." |
| Warning | irregular_rhythm | "Possible irregular rhythm detected - please share this with your doctor. This is not a diagnosis." |
| Warning | sleep_desats | "Breathing dips during sleep (N in 10 min) - discuss with your doctor." |
| Warning | fever_pattern | "Temperature and resting heart rate are both raised - possible fever or infection. Please check with a thermometer." |
| Warning | early_warning | "Several vital signs are outside their usual range." |
| Info | check_sensor_ecg / ppg | "ECG electrode not touching the skin - check electrodes and cable." / "SpO2 clip is not on the finger - check the clip." |
Critical alerts: buzzer + LED for 30 s with an "I'm OK" button; if not cancelled an SMS goes to every contact, text:
"SenseBand CRITICAL [name]: <message above>" (max 160 characters).

## 6.2 Hardware / piezo interface - YOU (mismatch to resolve)
The repository (docs/03_HARDWARE.md, `config/default.yaml`) specifies the grip sensor as an **FSR402 force-sensitive resistor**: 3.3 V -> FSR ->
ADS1115 input A1, with a **10 kOhm resistor from A1 to ground** (voltage divider), read at 250 Hz and converted to kg with a calibration table.
It does **not** describe a piezo with a 1 MOhm resistor and Zener diode, and the code has no piezo path (docs/03 only notes a piezo "gives
relative values"). Tell me what you actually built; if it is a piezo, the report's circuit and the software calibration claim must change.

## 6.3 Wiring / TMP102 ADD0 - YOU (mismatch to resolve)
The repository's temperature sensor is a **DS18B20** (1-Wire on GPIO4 with 4.7 kOhm pull-up) with **MLX90614** (I2C 0x5A) as the alternative.
**No TMP102 is used anywhere in the code**, so I cannot say where ADD0 goes. If you built with a TMP102 (I2C 0x48-0x4B depending on ADD0: GND=0x48,
V+=0x49, SDA=0x4A, SCL=0x4B) the driver must be added; confirm what is on your board.

## 6.7 Prototype status - YOU
I have not run anything on real hardware, so I do not know what the I2C scanner showed. The bring-up checklist (docs/03): `i2cdetect -y 1`
should show 0x48 (ADS1115) and 0x68 (MPU6050, plus 0x5A if an MLX90614). The ESP32 firmware has **never been compiled or flashed**.

## 6.8 Algorithms
- **Joint angle - FILLED:** the **Madgwick filter** (gyro + accelerometer -> quaternion) runs on both bands (`pipeline/madgwick.py`, `small_band.ino`).
  The angle is the rotation of the small band relative to the main band (relative quaternion) about the movement's axis, after a 3 s neutral
  calibration; a common heading drift cancels. (The old prototype used a complementary filter on pitch; it is replaced.) Simulated check: error under 1 degree.
- **Fall detection - FILLED:** the active method is a **rule**: free-fall (|a| < 0.4 g) then impact (> 2.5 g, or > 3.5 g alone) then stillness for
  10 s, then a 30 s "I'm OK" window. A LightGBM classifier (M2) exists but was trained **only on simulated falls**, is not validated, and is **disabled by default**
  (`models.fall.enabled: false`). No real fall dataset was available (SisFall unreachable).
- **Exercise suggestions - FILLED (rules) / approval YOU:** rule-based (`rehab/plan.py`, `config/exercises.yaml`), not ML. Stage from the % of normal range
  (< 40 % stage 1 early mobility, < 75 % stage 2 active range, else stage 3 strengthening); pain >= 5 steps down a stage, pain >= 7 gentle only and rest;
  first 14 days after injury stage 1; low adherence does not raise volume; targets never exceed the doctor's maximum angle. Plans are defaults marked
  "waiting for doctor approval" (stage 1 may start without a doctor, with a notice). **No physiotherapist has reviewed these rules** in this project: say who did, or state that they await review.

## 6.10 Cost - PARTIAL
Estimated list prices (INR, from docs/03, "verify locally"): Pi 3 (have) / Pi Zero 2 W 1,800; ESP32 350; 2 x MPU6050 300; MAX30102 250; AD8232 + electrodes
600; ADS1115 250; DS18B20 100 (MLX90614 900); FSR402 350; SIM800L + antenna 450; batteries, boost, chargers 1,200; button/buzzer/LED/wires/straps 300;
3D printing 500. Total about 6,000-6,700 excluding the Pi. **YOU:** replace with the prices you actually paid and the receipts.

## Not possible from here - YOU
Tables 5.1 / 5.4 / 7.1 results, section 6.9 measured power, references, front matter, signatures. The only measured numbers that exist are
software ones (simulator tests, model metrics on public datasets): see `CHANGELOG.md` and `ml/README.md`. The 8 h field test and the volunteer
study have not been done.

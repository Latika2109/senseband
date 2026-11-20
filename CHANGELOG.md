# Changelog

## Milestones 2-7 (2026-11-20) - built in one pass at the user's request, tested at the end
**Test status:** Python 208 tests (edge + ml), app 45 (vitest), cloud 32 (vitest on real PostgreSQL via PGlite) - all passing.
Lint, strict TypeScript and the production build are clean.

### Milestone 2 - App (local mode)
React + Vite + strict TypeScript PWA (Tailwind, Recharts, service worker + manifest). English and Hindi (a test enforces identical keys and
placeholders). Onboarding -> profile (validation, India-first phone numbers) -> pairing (same-origin/mDNS/hotspot auto-discovery, QR via the
browser BarcodeDetector, typed address) -> program -> purpose -> duration -> wear guide (inline SVG) -> signal check (gates ECG, finger clip,
motion sensor, SMS modem; an explicit acknowledgement is needed to start anyway) -> live dashboard (HR with "normal for the activity" label,
SpO2, temperature, rhythm in non-diagnostic words, ECG trace, HR-vs-expected chart, learning-period banner, "I'm OK" countdown banner).
Edge additions: CORS, `/api/pair`, `hr_range`, `countdown_left_s`, ECG strip endpoint, beta-blocker from the medicine list.

### Milestone 3 - Rehab engine + recovery UI
Relative-quaternion angles for 13 movements of 6 joints (common yaw cancels), neutral-pose calibration with a steadiness check, ROM / grip / pain
assessments, guided exercise (gauge, target zone, rep + tempo + hold, doctor-limit stop with haptics, pain >= 7 stops), exercise library (YAML) and
stage-based plan generator that never exceeds the doctor's limit, saturating-exponential recovery forecast with bootstrap interval (>= 5 days),
progress screen. Watchdogs end a calibration/grip test when the band or sensor is missing.

### Milestone 4 - Cloud + sharing
Supabase schema mirroring the edge tables, RLS, care-team invites, revocation, device-key ingest (`ingest_batch`: idempotent, one patient only,
cannot touch another patient's rows), `pull_changes`, DPDP erasure, triage view; Edge Functions `ingest`/`pull`/`notify` (web push + cloud-SMS
fallback). Edge sync worker: coalescing, event-first priority, row+byte batching, exponential backoff, parked rows after repeated rejection,
cursor-based pull of profile/plans/limits/notes (cloud wins once a human edited them). App: phone-OTP sign-in, doctor patient list sorted by
severity, plan editor bounded by the stored limit, notes, care team, Realtime toasts, web push registration, "link this band to the cloud".
The band now generates a unique persistent patient id (it is the cloud key).

### Milestone 5 - ML models (real public data; see `ml/README.md`)
`ml/senseband_features` is the single feature implementation (training and edge import the same functions; a test asserts object identity).
`make train` downloads UCI HAR, PAMAP2 and CinC 2017 (guarded extraction), trains LightGBM models with subject-wise CV, exports ONNX with
parity/size/latency checks, writes `metrics.json`/`METRICS.md`. Results: M1 macro-F1 0.851 (rules 0.731, target 0.90 **not met**), M3 accuracy
0.957 (target met), M4 AF-F1 0.741 (old RR rule 0.27, target 0.80 **not met**), M5 MAE 11.9 -> 10.1 bpm with per-user RLS (target 8 **not met**),
M2 trained on **simulated falls only** (SisFall unreachable) and kept **disabled** on the edge. Edge: registry with automatic fallbacks (missing,
version-mismatched, oversized, failing or slow models switch themselves off), M1 with a rule override for running, M3/M4/M5 integrated, M2 veto
hook, personal-HR learning persisted, "Record training data" mode, optional rolling raw store.

### Milestone 6 - Reports + polish
PDF reports (reportlab; HR vs expected, SpO2, activity, alerts with SMS/ack status, ECG strips, notes, recovery progress with adherence and an
honest forecast), data export (ZIP with checksummed manifest) and permanent erase (verified: deleted strings are gone from the database and WAL
files), rotating logs, ECG duty-cycling during sleep (opt-in), Ed25519-signed OTA with atomic switch and automatic rollback, systemd units,
install/power scripts, health marker. 24 h simulated run: through the real engine (ML models on) into a PDF: 1440 minutes, 99.9 % coverage, HR 54.6 / 69.8 / 152.7 (min/avg/max), resting HR 65.3, SpO2 min 90, sleep 10.4 h (scenario: 10.5 h), 40 min running, 80 min walking, 0 errors, no false alerts (the 4+2 designed desaturation dips were too far apart to count as a pattern). Simulated data only; the 8 h field test on a real band is **not done**.

### Milestone 7 - Field validation (tooling only)
`docs/07_VALIDATION_PROTOCOL.md`, CSV templates, `python -m senseband_edge.validation` (Bland-Altman, ARMS, tolerance rates, pass/fail with
caveats, false-alarm rate, battery projection), a WebSocket logger, a simulator false-alarm benchmark. **The study with volunteers has not been
run; no real-world accuracy exists yet and the README says so.**

### Bugs the tests caught (all fixed)
- WebSocket `event` messages lost their type tag (alert time overwrote `t`) - field renamed `raised_t`.
- `parsePairText` accepted `ftp://x` as a hostname.
- Power-manager hook compared inverted states and reset the ECG every second, so the ECG never left "collecting" (would have disabled all
  ECG monitoring) - found by the API test, fixed before the 24 h run.
- "Too fast" rep detection compared against the raw tempo although reps are timed between 20 % crossings (a perfectly paced rep was flagged);
  speed is now measured over ~0.2 s so sensor jitter cannot trigger it.
- Calibration/grip tests could wait forever with no band/sensor; noise from a noisy ECG segment leaked into HR/RR after the noise ended.

### Known limitations
The band's local API is unauthenticated (LAN-trust only; see README). Trained `.onnx` files are committed in `ml/models/` (re-create with `make train`).
Pi drivers, BLE link, SIM800L, GPIO and the ESP32 firmware are untested on hardware (firmware never compiled); Supabase Auth/Realtime/Storage,
web push and SMS providers untested on a live project; PDF reports are English only (Devanagari needs a font file); battery life, Pi 3
latency/RAM and all accuracy figures on real people are unmeasured.


## Milestone 1 - Edge service (FastAPI) on Pi + simulator (2026-11-20)
- `edge/senseband_edge/` production edge service. Config in `edge/config/default.yaml`
  (validated by pydantic; every threshold, pin, SMS number and path lives there).
- **Pipeline**: ECG quality gate (lead-off / noisy / flat / good) + HR + RR variability; activity and
  posture from the waist IMU; fall detector (free-fall -> impact -> still); small-band protocol v2
  parser (`Q`, `V` + perfusion, `B`); Madgwick filter. Poor-quality data is flushed so it can never
  leak into HR/RR.
- **Context engine + alerts** (rule-based fallbacks for M1-M5, M10): HR high/low at rest (with a
  post-exertion recovery allowance), HR very high in exercise, SpO2 warning/critical, sleep
  desaturation events, possible-irregular-rhythm, fever pattern, early-warning score, collapse
  (contact OK but no heartbeat/pulse/movement), fall, SOS. Poor signal gives a "check sensor" notice
  instead of a vital alert. Messages never diagnose.
- **Critical flow**: buzzer + LED, 30 s "I'm OK" window (button or `POST /api/imok`), then SMS to all
  contacts with 3 retries; one SMS per alert; 10 min de-dup per kind; ECG strip saved per event.
- **Storage**: SQLite WAL, versioned migrations, all tables from docs/05, outbox mirroring every
  write (blobs as base64), 1-minute `vitals_minute` aggregates, programs, patient profile.
- **API**: `/api/status`, `/signal-check`, `/patient` (GET/PUT), `/programs` (+stop), `/events/{id}/ack`,
  `/imok`, `/sos`, `/history`; `/ws/live` with `vitals` (1 Hz), `ecg` (5 Hz), `event`, `status`.
- **Drivers** (ported from the prototype, lazily imported so the package runs anywhere): MPU6050,
  ADS1115, DS18B20/MLX90614, SIM800L, BLE link with command channel, GPIO button/buzzer/LED,
  `HardwareSource`. Any missing sensor degrades instead of crashing (tested).
- **Firmware** `small_band.ino`: Madgwick quaternion (`Q`, 10/25/50 Hz), perfusion in `V`, battery
  `B`, command characteristic (`RATE`, `VIB`, `CAL`). Replaced the complementary-filter `I` message.
- **Simulator additions**: `SmallBandEmulator` (protocol v2 + commands). `make sim` now runs the whole
  edge service on simulated sensors; the old raw stream is `make sim-stream`.
- 59 tests total (40 new): scenario acceptance, REST/WebSocket, storage, parser, filters, degradation.

**Not verified / open**: drivers and firmware have not run on real hardware (firmware not compiled -
no toolchain here); RR/HR feature code still lives in `pipeline/` and moves to
`ml/senseband_features` in Milestone 5 when models are trained; slow-HR-recovery rule and
exercise/session endpoints are Milestone 3; cloud sync worker is Milestone 4.

## Milestone 0 – Repo + simulator (2026-11-20)
- Monorepo scaffolding: `pyproject.toml` (Python 3.11, ruff, pytest), `Makefile`
  (`setup`, `sim`, `sim-list`, `lint`, `test`), `.pre-commit-config.yaml`, `.gitignore`.
- `edge/senseband_edge/hw/frames.py`: sensor data types shared by simulator and future real drivers.
- `edge/senseband_edge/hw/sim/`: hardware simulator, fully driven by YAML
  (`sim_config.yaml` for sensors/noise/activities, `scenarios/*.yaml` for timelines).
  - ECG (250 Hz): normal, atrial fibrillation (irregular RR, no P wave), muscle/motion noise,
    lead-off (mains hum + LO flag), asystole.
  - PPG (25 Hz): red/IR counts with SpO2 encoded in the ratio-of-ratios; finger-off; motion artifact.
  - 3 IMUs (50 Hz): waist, reference limb (main band), moving limb (small band) with accel, gyro,
    quaternions; gait, breathing, fall (free-fall + impact), collapse, hinge-joint exercises.
  - Temperature (0.2 Hz, DS18B20 resolution) and grip (250 Hz, only during a grip test).
  - Ground-truth labels on every frame (`Truth`) for tests and ML training data.
  - `ble_lines`: emulates the ESP32's `I,...` / `V,...` text protocol.
  - 13 scenarios: resting, jogging, sleeping, fall, collapse, low_spo2, atrial_fibrillation,
    lead_off, noisy_ecg, resting_tachycardia, elbow_exercise, knee_exercise, grip_test.
- `make sim SCENARIO=jogging` streams a status line every 10 s plus a summary; `--speed 0` runs
  as fast as possible (10 min of jogging in ~5 s), `--speed 1` is real time.
- 19 tests in `edge/tests/test_sim.py` check each scenario's physiology, e.g. fall
  free-fall < 0.4 g then impact > 2.5 g, joint angle from the relative quaternion within 2 deg,
  SpO2 recoverable from raw PPG.

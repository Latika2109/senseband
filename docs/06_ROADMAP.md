# 06 – Roadmap (work in this order)

## Milestone 0 – Repo + simulator
- [x] Monorepo layout, Makefile, ruff/pytest, pre-commit (hook config added; run `pre-commit install` once the folder is a git repo)
- [x] `hw/sim/` (at `edge/senseband_edge/hw/sim/`): synthetic IMU (waist + 2 limb IMUs), ECG (normal, AF, noise, lead-off), PPG/SpO2,
      temp, grip; scenario files (resting, jogging, sleeping w/ desaturations, fall, collapse, elbow & knee exercise)
- **Done when:** `make sim SCENARIO=jogging` streams plausible data for 10 min without errors.

## Milestone 1 – Edge service (FastAPI) on Pi + simulator
- [x] Port drivers from `edge/prototype` (sensors.py, comms.py), add buttons/buzzer/LED (**untested on real hardware**; laptop tests cover everything above the driver layer)
- [x] SQLite schema + migrations + outbox (05), programs/sessions
- [x] Signal-quality gates, rule-based context engine + alerts (fallbacks for all models)
- [x] REST + `/ws/live`, `/status`, `/signal-check`; "I'm OK" 30 s cancel flow; SIM800 SMS with retries
- [x] Firmware: quaternion (Madgwick) + battery + command characteristic (written, **not yet compiled or flashed** - no toolchain in dev env)
- **Done when:** on the simulator, the fall scenario sends one SMS (mock) after 30 s unless cancelled;
  jogging produces no HR alert; resting-tachycardia scenario produces a warning. Runs on the Pi 3 < 400 MB RAM.

## Milestone 2 – App (local mode)
- [x] PWA shell, i18n (en, hi), connection to band (auto-discover / QR) (service worker + manifest; QR scan uses the browser BarcodeDetector, typed address is the fallback)
- [x] Onboarding, profile, choose program, monitoring: purpose → duration → wear guide → signal check → live dashboard
- [x] Alerts list + acknowledge
- **Done when:** a new user can go from first launch to a live monitoring dashboard on a phone in < 3 min.
  *Evidence:* an automated test completes the whole flow (9 screens, ~15 inputs, no waiting but the 4 s ECG settle). **Not timed with a real person on a real phone.**

## Milestone 3 – Rehab engine + recovery UI
- [x] Relative-quaternion joint angles for 6 joints, calibration, movement definitions
- [x] Exercise library (per joint), plan generator (stages), doctor limits (library texts are English; names also in Hindi)
- [x] Assessment screens (ROM, grip, pain), guided exercise (gauge, target zone, reps, tempo, haptics), pre/post checks
- [x] Progress charts + recovery forecast (≥ 5 sessions)
- **Done when:** angle error ≤ 5° vs a goniometer at 0/45/90° for elbow and knee; reps counted ±1.
  *Evidence:* < 1° against the simulator's ground truth for every movement at 0/45/90° (also with a common yaw drift), reps exact on the
  simulator. **A real goniometer comparison is still to do** (Milestone 7 protocol).

## Milestone 4 – Cloud + sharing
- [x] Supabase schema, RLS, auth (phone OTP), care-team invites (RLS/invites verified on real PostgreSQL 16; OTP needs a live Supabase project)
- [x] Edge sync worker (push measurements, pull plans/profile), resumable after outages
- [x] App cloud mode, doctor view (patient list by alert severity, edit plan, notes), Realtime alerts, web push (UI tested against a fake API; Realtime/web push untested on a live project)
- **Done when:** unplugging internet for 2 h then reconnecting syncs everything with no duplicates;
  a doctor account sees only invited patients.
  *Evidence:* a simulated 2 h outage (10 s ticks, backoff, then reconnect) delivers every row exactly once; lost responses and mid-batch
  crashes resend idempotently; the doctor-sees-only-invited-patients rule is tested in SQL. **Not run against live Supabase.**

## Milestone 5 – ML models
- [x] Dataset download/convert scripts, shared features package
- [x] Train M1–M5, subject-wise CV, ONNX export, metrics json, Pi 3 latency check (latency measured on a laptop; `make bench` must be run on the Pi 3)
- [x] Integrate into context engine (models replace fallbacks when present); "Record training data" mode
- **Done when:** targets in 04_ML_MODELS.md met (or gaps documented), all inference < 50 ms on Pi 3.
  *Evidence:* targets met for **M3 only**; **M1 (0.85 vs 0.90), M4 (AF F1 0.74 vs 0.80) and M5 (MAE 10.1 vs 8 bpm) are not met** and **M2 was trained on
  simulated falls only (SisFall unreachable) so it is disabled on the edge**. Gaps and next steps: `ml/README.md`. Inference is 0.02–0.1 ms on a laptop;
  the Pi 3 figure is unmeasured.

## Milestone 6 – Reports + polish
- [x] Daily/program PDF reports with ECG strips, adherence, forecast
- [x] Battery optimizations, systemd autostart, log rotation, OTA update script, data export & delete (units/scripts syntax-checked, never run on a Pi; ECG duty-cycling is off by default)
- **Done when:** a 24 h simulated run produces a correct report and a field test of 8 h passes.
  *Evidence:* 24 simulated hours through the real engine -> report (see CHANGELOG for the numbers). **The 8 h field test has not been done.**

## Milestone 7 – Field validation
- [ ] Compare against a commercial pulse oximeter, BP-cuff HR and a goniometer on 5–10 volunteers  **NOT DONE** – needs people and hardware; protocol, templates, analysis tool and logger are ready (`docs/07_VALIDATION_PROTOCOL.md`)
- [ ] Document accuracy, false-alarm rate, battery life → update README  **NOT DONE** (the README says so explicitly). Simulator false-alarm benchmark exists as a regression guard only

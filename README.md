# SenseBand

All-in-one, offline-first wearable for **continuous health monitoring** and **fracture / accident
recovery tracking** – built for elderly people, rural areas without regular doctor visits,
post-discharge and international patients, and athletes.

**Main band** (Raspberry Pi 3, waist or limb): ECG, IMU, temperature, grip sensor, SIM800L SMS.
**Small band** (ESP32, wrist/limb/finger clip): SpO2 + heart rate (MAX30102), IMU. Linked by Bluetooth LE.

## What makes it different
1. **Live processing on the band** – no internet needed to detect a problem.
2. **Offline-first** – stores everything locally, syncs to the cloud when possible, SMS when not.
3. **Context-aware** – heart rate is judged against what the person is doing
   (jogging vs sitting vs sleeping) and against *their own* normal, not a fixed number.
4. **Two products in one** – 24/7 vitals monitoring *and* measured rehab (range of motion,
   grip strength, rep quality, recovery forecast) with one set of hardware.
5. **One shared patient profile** – patient, family and doctor see the same data from anywhere.

## Status (honest)
| Milestone | Built | How it was checked |
|---|---|---|
| 0 Simulator | 15 scenarios (resting, jogging, sleeping, fall, collapse, AF, lead-off, exercise, a 24 h day...) | pytest on the physiology of each scenario |
| 1 Edge service | FastAPI + WebSocket, SQLite + outbox, rules, 30 s "I'm OK" -> SMS with retries | pytest on simulator scenarios and the REST/WS API |
| 2 App | React PWA (English + Hindi), onboarding, pairing (auto/QR/typed), live dashboard, alerts | vitest incl. the full first-launch flow |
| 3 Rehab | 6 joints, calibration, ROM/grip/pain, guided exercise, plans, doctor limits, forecast | pytest (angle error < 1 deg vs simulator truth), vitest |
| 4 Cloud | Supabase schema + RLS + invites, sync worker, doctor view, Realtime, web push | RLS/ingest on **real PostgreSQL 16** (PGlite); sync vs a mock cloud |
| 5 ML | shared features, 5 ONNX models, record mode | trained on real public datasets, subject-wise CV (see below) |
| 6 Reports | PDF reports, export/erase, log rotation, signed OTA + rollback, systemd units | pytest (numbers, safety, OTA attacks); a 24 h simulated run |
| 7 Validation | protocol, templates, analysis toolkit, false-alarm benchmark | toolkit tested on synthetic data. **The study itself has not been run** |

**Not verified yet:** the Pi drivers and BLE link on real hardware; the ESP32 firmware (written, never compiled or flashed);
Supabase Auth/Realtime/Storage/Edge Functions on a live project (`cloud/README.md`); web-push and SMS providers; battery life;
and every accuracy claim on real people (`docs/07_VALIDATION_PROTOCOL.md`).

**Security limitation:** the band's local API (REST + WebSocket) has **no authentication**. Anyone on the band's network can read the
data, cancel or send an alert, or erase it. Run it only on the band's own WPA2 hotspot or a trusted home network, never on the open
internet; a per-band pairing token (carried in the QR) is the next thing to add. The cloud path is authenticated (device key for the band,
Supabase Auth + RLS for people).

### Measured accuracy
*Of the models on public datasets only (not on SenseBand hardware, not on volunteers):* see `ml/README.md`.
*Of the band on people:* **not yet measured.** Do not quote any accuracy for SenseBand until `VALIDATION_REPORT.md` exists.

## Quick start (laptop, no hardware)
```bash
make setup && make test                    # Python 3.11 venv, lint + tests
(cd app && npm install && npm run build)   # the PWA, served by the edge service itself
make sim SCENARIO=fall                     # http://localhost:8080 - the app, API (/api/status) and live WebSocket
make train                                 # optional: download datasets and train the models (needs ~1 GB)
```

## Using this folder with Claude Code
```bash
cd senseband
claude
> Read CLAUDE.md and the docs, then start Milestone 0.
```
Then go milestone by milestone (`docs/06_ROADMAP.md`). Build the hardware in parallel with
`docs/03_HARDWARE.md`; the software runs on simulated sensors until the hardware is ready.

## Docs
| File | Contents |
|---|---|
| docs/01_PRODUCT_FLOW.md | screens and user journeys for both modes |
| docs/02_ARCHITECTURE.md | system design, tech stack, sync, alert path |
| docs/03_HARDWARE.md | wiring, BOM, power, how to wear for each joint |
| docs/04_ML_MODELS.md | context engine and the models, datasets, training plan |
| docs/05_DATA_MODEL_AND_API.md | tables, REST + WebSocket API |
| docs/06_ROADMAP.md | milestones and acceptance criteria |
| docs/WIFI_AND_TWILIO.md | ESP32 over Wi-Fi to the Pi + Twilio SMS: exact setup |
| docs/RUN_ON_HARDWARE.md | step-by-step for a friend: flash the ESP32, wire and install on the Pi, first checks |
| docs/07_VALIDATION_PROTOCOL.md | how to measure real-world accuracy, false alarms and battery |
| ml/README.md | models, data, results and gaps |
| cloud/README.md | Supabase setup and what is / is not verified |
| edge/deploy/ | systemd units, install script, config example for the Pi |

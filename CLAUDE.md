# CLAUDE.md – instructions for Claude Code

You are building **SenseBand**: a two-band wearable (Raspberry Pi main band + ESP32 small band) and its app,
for **remote health monitoring** and **accident / fracture recovery tracking**. It is offline-first:
everything is processed live on the band, saved locally, synced to the cloud when internet exists,
and critical alerts go out by SMS when it does not.

## Read these first (in order)
1. `docs/01_PRODUCT_FLOW.md` – what the user sees, every screen and flow
2. `docs/02_ARCHITECTURE.md` – components, tech stack, data flow, sync
3. `docs/03_HARDWARE.md` – pin map, BOM, power, wearing positions (source of truth for pins)
4. `docs/04_ML_MODELS.md` – context engine + every model, datasets, training, deployment
5. `docs/05_DATA_MODEL_AND_API.md` – database tables, edge API, WebSocket messages
6. `docs/06_ROADMAP.md` – milestones with acceptance criteria. **Work milestone by milestone.**

## Repo map
```
firmware/small_band/   ESP32 Arduino sketch (working prototype – extend, don't rewrite)
edge/prototype/        Working Flask prototype for the Pi. Reuse sensors.py / processing.py / comms.py
edge/senseband_edge/   NEW: production edge service (FastAPI) – you create this (Milestone 1)
app/                   NEW: React + Vite + TypeScript PWA (Milestone 2+)
cloud/                 NEW: Supabase schema, RLS policies, edge functions (Milestone 4)
ml/                    NEW: datasets, feature code, training, ONNX export (Milestone 5)
```

## How to work
- Before each milestone: restate its acceptance criteria, make a short plan, then build.
- After each milestone: run tests, update `docs/06_ROADMAP.md` checkboxes, and write a short
  `CHANGELOG.md` entry. Stop and summarize for the user before starting the next milestone.
- **Hardware simulator is mandatory** (Milestone 0). Everything must run on a laptop with
  `SENSEBAND_SIM=1` and no sensors attached, including scenarios: resting, jogging, sleeping,
  fall, low SpO2, atrial fibrillation, lead-off, exercise session for elbow/knee.
- Python 3.11, type hints, `ruff` + `pytest`. TypeScript strict, ESLint, Vitest.
- Feature-extraction code lives in **one shared package** (`ml/senseband_features/`) used by BOTH
  training and the edge service – never duplicate it (prevents train/serve skew).
- All thresholds, model paths, pins and phone numbers come from config files, never hard-coded.
- The edge must keep working if the cloud, internet, BLE link, or any single sensor is missing.
- Target hardware is a **Raspberry Pi 3 (1 GB RAM)**: keep total edge RAM < 400 MB,
  models < 5 MB each, inference < 50 ms per window. Use ONNX Runtime, not TensorFlow/PyTorch, on the edge.

## Safety and wording rules (important)
- This is a **monitoring aid, not a medical device**. UI text must never diagnose
  ("possible irregular rhythm – share with your doctor", not "you have AF").
- Exercise plans are **defaults that a doctor/physio must approve or edit**; respect the
  doctor-set maximum angle for each joint and stop the session if the patient reports pain ≥ 7/10.
- Never auto-delete patient data. Data export and account deletion must exist (India DPDP Act 2023).
- Signal-quality gating: never raise a vital-sign alert from a signal flagged as poor quality
  (lead-off, finger-off, motion artifact). Raise a "check sensor" notice instead.
- Critical alerts always use SMS (SIM800L) in addition to app/cloud notifications.

## Commands
```
make setup          # create .venv (Python 3.11) and install dependencies
make sim            # edge service on simulated sensors: make sim SCENARIO=fall SPEED=1  (API http://localhost:8080)
make sim-stream     # raw simulated sensor stream only:  make sim-stream SCENARIO=jogging
make sim-list       # list scenarios
make edge           # edge service on the Pi (real sensors)
make app            # PWA dev server (app/): cd app && npm i && npm run dev   (build: npm run build -> served by the edge)
make test           # lint + all Python tests (edge + ml); also: (cd app && npm test), (cd cloud && npm test)
make train          # download datasets, train M1-M5, write ml/models/*.onnx + metrics.json + METRICS.md
make bench          # model latency (run on the Pi 3)
```

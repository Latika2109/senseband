# 02 – Architecture

```
┌──────────── SMALL BAND (ESP32) ────────────┐
│ MAX30102 (PPG 25 Hz) → HR, SpO2, quality   │
│ MPU6050 (50 Hz) → Madgwick quaternion      │──── BLE notify ────┐
│ battery %, status                          │                    │
└────────────────────────────────────────────┘                    ▼
┌──────────────────── MAIN BAND (Raspberry Pi 3) – edge service (Python, asyncio) ───────────────────┐
│ Acquisition: ECG 250 Hz (ADS1115), IMU 50 Hz, temp 0.2 Hz, grip 250 Hz (on demand), BLE, buttons   │
│ Signal quality → Feature windows (shared ml/senseband_features) → ONNX models                      │
│ CONTEXT ENGINE: activity + intensity + posture + time + recent history + personal baseline         │
│ Rehab engine: joint angle (relative quaternion), reps, tempo, assessments, plan, forecast          │
│ Alert manager: severity, de-dup, escalation, "I'm OK" cancel, SIM800L SMS                          │
│ Storage: SQLite (WAL) + outbox ── Sync worker ──(when online)──► Cloud                             │
│ Local API: FastAPI REST + WebSocket /ws/live, serves the PWA build; Wi-Fi hotspot fallback          │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
          │ local Wi-Fi/hotspot (live, works offline)                │ HTTPS when online
          ▼                                                          ▼
┌──────── APP (React PWA) ────────┐                    ┌──────── CLOUD (Supabase) ────────┐
│ Local mode: talks to the band   │◄── same codebase ─►│ Postgres + Auth + Row Level Sec. │
│ Cloud mode: talks to Supabase   │                    │ Realtime (alerts), Storage (ECG  │
│ Patient / caregiver / doctor UI │                    │ strips, PDF reports), Edge Fns   │
└─────────────────────────────────┘                    │ (push notifications)             │
                                                        └──────────────────────────────────┘
```

## Tech stack (decided – change only with reason)
| Part | Choice | Why |
|---|---|---|
| Small band | Arduino C++ on ESP32, BLE GATT | already working; low power |
| Edge | Python 3.11, FastAPI + uvicorn, asyncio, SQLite (WAL), onnxruntime, numpy/scipy | runs on Pi 3; one process |
| ML training | Python, scikit-learn / LightGBM, optional small 1-D CNN in PyTorch → **ONNX** | small, fast models for Pi 3 |
| App | React + Vite + TypeScript, Tailwind, Recharts, PWA (installable), i18n (English + Hindi first) | one codebase for phone + laptop, no app store needed; wrap with Capacitor later for Android |
| Cloud | Supabase (Postgres, Auth, RLS, Realtime, Storage, Edge Functions) | free tier, SQL, role-based access for doctor/family |
| SMS | SIM800L on the band (offline); optional cloud SMS (MSG91/Twilio) via Edge Function when online | works without internet |

## Key design rules
1. **Edge is the source of truth for measurements**; cloud is the source of truth for profile,
   care team, plans and doctor edits (pulled down by the edge on sync).
2. **UUIDs generated on the edge**, idempotent upserts → safe re-sync after any outage.
3. **Bandwidth-light sync** (rural): upload 1-minute aggregates, events, assessments, sessions,
   and 30-s ECG strips only around events – not raw streams. Raw data stays on the band (rolling
   7 days) and can be uploaded on request over Wi-Fi.
4. **App modes**: if the band is reachable on the local network → Local mode (live, 5 Hz); otherwise
   Cloud mode (near-live via Realtime when the band is online, history always).
5. **Device auth**: each band has a device key bound to one patient; it can only insert that patient's data.
6. **Graceful degradation**: missing sensor/BLE/internet/SIM never crashes the service; status is reported
   in `/api/status` and shown in the app.

## Edge service internals (modules)
```
senseband_edge/
  config.py            settings (pydantic-settings, YAML + env)
  hw/                  drivers: mpu6050, ads1115, temp, sim800, buttons_buzzer, ble_link; sim/ = simulator
  pipeline/            ring buffers, windowing (2.56 s windows, 50 % overlap), quality gates
  context/             context engine (activity, intensity, expected HR, baselines)
  models/              ONNX loaders with rule-based fallbacks
  rehab/               joint angles, exercises, assessments, plan generator, forecast
  alerts/              rules, early-warning score, escalation, SMS
  storage/             SQLite schema + migrations, outbox
  sync/                Supabase client, push/pull
  api/                 FastAPI routers + WebSocket
  main.py
```

## Critical alert path (must work with zero internet)
detect → buzzer + LED, 30 s "I'm OK" window → SMS to all contacts via SIM800L (retry 3×) →
log event + ECG strip → when online: push to cloud → Realtime → caregiver/doctor app.

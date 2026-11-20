# 04 – Context engine and ML models

Goal: alerts that understand **context** (what the person is doing, their own normal, signal quality),
running on a Raspberry Pi 3. Every model has a **rule-based fallback** so the system works before
models are trained.

## The context engine (every 2.56 s window, 50 % overlap)
```
IMU (waist) ─► M1 activity + intensity + posture ─┐
ECG ─► M3 quality ─► HR, RR intervals ─► M4 rhythm├─► M5 expected HR (personal) ─► residual
PPG ─► quality ─► HR, SpO2 ─► desaturation events  │
temp, time of day, sleep state, last 5 min activity┘
                               │
                               ▼
         Decision rules + early-warning score → info / warning / critical
```
Examples of context rules (implement as data-driven rules, unit-tested with simulator scenarios):
| Situation | Decision |
|---|---|
| HR 150 while running/jogging, intensity high | normal – label "Exercise" |
| HR 150 while lying/sitting for > 3 min (after recovery allowance) | warning; critical if > 170 or with abnormal ECG |
| Exercise ended, HR drops < 12 bpm in first minute | warning "slow heart-rate recovery" (known cardiac risk marker) |
| HR suddenly drops / ECG flat + impact/fall + no movement | critical (possible collapse – athlete scenario) |
| SpO2 drops ≥ 4 % from baseline repeatedly while lying at night | warning "breathing dips during sleep – discuss with doctor" (oxygen desaturation index) |
| Temp up + resting HR up vs baseline for > 30 min | warning "possible fever/infection" |
| Any vital from a poor-quality signal | no vital alert; "check sensor" notice |
| Patient on beta-blockers (profile) | lower expected HR range |

## Models
| # | Model | Input | Output | Training data | Algorithm (edge) | Target |
|---|---|---|---|---|---|---|
| M1 | **Activity & posture** | waist IMU window (50 Hz) | lying / sitting / standing / walking / stairs / running / exercising + intensity | UCI HAR (waist, 30 people, 50 Hz), WISDM, PAMAP2; + own recordings | LightGBM on time/frequency features (ONNX) | macro-F1 ≥ 0.90 on held-out subjects |
| M2 | **Fall detection** | 4 s IMU window around an impact trigger | fall / not fall | **SisFall** (waist-mounted, includes elderly ADLs), MobiFall | rule trigger + LightGBM | sensitivity ≥ 95 %, < 1 false alarm/day |
| M3 | **ECG signal quality** | 5 s ECG | good / noisy / unusable | PhysioNet/CinC 2011 quality, MIT-BIH + Noise Stress Test (add real noise) | LightGBM on SQI features (kurtosis, power ratios, template corr.) | accuracy ≥ 0.92 |
| M4 | **Rhythm screening** | 30 s single-lead ECG / RR intervals | normal / possible AF / other / noisy | **PhysioNet/CinC 2017** (single lead like AD8232), MIT-BIH AF | LightGBM on RR features (RMSSD, pNN50, sample entropy, CoSEn) | AF F1 ≥ 0.80 |
| M5 | **Expected HR (personal)** | activity, intensity, cadence, posture, last-5-min load, time of day, temp | expected HR + band | PAMAP2 (HR + IMU) for the population prior; then **per-user online update** during the 48 h learning period | gradient-boosted regressor (population) + per-user linear correction (recursive least squares) | MAE ≤ 8 bpm after personalization |
| M6 | **SpO2 quality & desaturation** | PPG ratio, perfusion, motion | valid SpO2 / desat events | rules (no training) | rules | – |
| M7 | **Rehab rep & tempo** | joint-angle time series | reps, peak angle per rep, speed, hold time | own recordings | peak detection + hysteresis (no training) | ±1 rep, ±5° vs goniometer |
| M8 | **Recovery forecast** | ROM per session vs days since injury | predicted ROM curve, date to reach target, confidence | patient's own sessions | saturating exponential fit ROM(t)=R∞−(R∞−R0)e^(−kt), bootstrap CI; ≥ 5 sessions | shown as estimate only |
| M9 | **Exercise plan** | joint, % of normal ROM, pain, days since injury, adherence, doctor limits | stage + exercise list | protocol table (docs) | rules (doctor editable) – not ML | – |
| M10 | **Early-warning score** | HR, SpO2, temp, consciousness proxy (no movement after fall) | 0–N score | NEWS2 subset | rules | – |

Normal ROM reference (approx., AAOS): shoulder flex 180, abd 180; elbow flex 150, pronation 80,
supination 80; wrist flex 80, ext 70; hip flex 120, abd 45; knee flex 135; ankle dorsi 20, plantar 50.

## Training pipeline (`ml/`)
```
ml/
  senseband_features/     shared feature code (imported by edge too)
  datasets/               download + convert scripts → common format (parquet: ts, ax..gz, ecg, label, subject)
  train_activity.py  train_fall.py  train_ecg_quality.py  train_rhythm.py  train_expected_hr.py
  eval/                   subject-wise cross-validation, confusion matrices, reports (markdown)
  models/                 <name>-<version>.onnx + <name>-<version>.json (metrics, features, sample rate)
  record/                 tools to label own recordings from the app's "Record training data" mode
```
Rules:
- Resample every dataset to the band's rates (IMU 50 Hz, ECG 250 Hz) and units (g, deg/s, mV-scaled).
- **Split by subject**, never by window (no leakage).
- Augment IMU with random rotations (band orientation differs), ECG with real noise records.
- Export to ONNX, verify ONNX output == sklearn output on a test batch, check RAM/latency on a Pi 3.
- Datasets need a laptop with internet (PhysioNet, UCI). Respect each dataset's licence; PhysioNet
  credentialed datasets are not required for the ones listed.

## Own data collection (needed – public data ≠ our sensors)
App → Settings → "Record training data": choose label (lying, sitting, walking, jogging, stairs,
fall-on-mattress, exercise X) → records raw IMU/ECG/PPG with labels. Target: 10+ volunteers ×
3 min per activity; falls only onto a crash mat. Fine-tune M1/M2 and recheck metrics.

---
## Status after Milestone 5 (see `ml/README.md` and `ml/models/METRICS.md` for the generated numbers)
- Datasets actually used: **UCI HAR + PAMAP2** (M1, M5), **PhysioNet CinC 2017** (M3, M4; CinC 2011 and SisFall were not reachable, MIT-BIH has a
  download script but is unused). M2 therefore trains on simulated falls and is disabled on the edge until it is validated on real falls.
- Targets met: M3. Not met: M1 (0.85 vs 0.90), M4 (AF F1 0.74 vs 0.80), M5 (MAE 10.1 vs 8 bpm). Each beats its rule-based fallback on the same data
  (M1 0.73, M4 0.27), which is why the models replace the rules when present.
- The edge imports the same feature code as training (`ml/senseband_features`, `FEATURE_VERSION` 2) and refuses models trained on another version.
- Deviations from the plan above: M1 adds a `stairs` class and a raw-energy rule that keeps "running"; M5 is a LightGBM regressor plus an RLS
  correction with a 25 % warm-up in evaluation; M3 has a third class `unusable` made from simulated flat/hum/clipped windows.

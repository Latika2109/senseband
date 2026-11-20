# 07 – Field validation protocol (Milestone 7)

**Status: NOT YET RUN.** Everything here is the plan and the tooling. No volunteer has worn the band, no accuracy
number from people exists, and nothing in this repository should be quoted as a measured accuracy until the
steps below have been done and `VALIDATION_REPORT.md` exists. Numbers produced on the simulator
(`false_alarms_sim.py`, the model metrics on simulated data) are regression guards, not evidence.

SenseBand is a monitoring aid, not a medical device. This study measures how well it agrees with reference
instruments; passing it is not a medical clearance.

## Before you start
- **Ethics and consent.** Get approval from your institution's ethics committee (or document why it is exempt),
  written informed consent from each volunteer, and a plan for the data (India DPDP Act 2023: collect only what
  you need, name a custodian, allow withdrawal and deletion – `Settings > Delete my data` erases a band).
- **Safety.** Volunteers are healthy adults. Nobody is made hypoxic. Falls are *simulated* by a trained
  person onto a crash mat with a spotter, never on bare floor. Exercise stops on any discomfort.
- **People: 5 to 10** with a spread of skin tone, age (include some over 60 if possible) and wrist/arm size.
  Record them as V01, V02, … with no names in the data files.
- **Equipment:** a clinical-grade fingertip pulse oximeter (CE/FDA-cleared) and a validated upper-arm BP monitor
  that shows pulse (or a chest-strap HR monitor), a plastic goniometer (two raters), a stopwatch, a crash mat,
  a calibrated thermometer, a luggage scale for the grip plier, a laptop on the band's network.
- **Band prep:** `make train` models present, bands charged, clocks synced (`timedatectl`), `power.ecg_duty_cycle: false`.

## Measurements
| # | Test | Reference | Protocol | Pass criterion (`validation/stats.py`) |
|---|---|---|---|---|
| 1 | **Heart rate** | BP-monitor/chest-strap pulse | per volunteer: 5 min seated rest, 10 min walk, 3 min jog, then 5 min recovery. Read the reference every 30–60 s and log the band with `validation.logger`. | MAE ≤ 5 bpm and ≥ 90 % within ±5 bpm |
| 2 | **SpO2** | fingertip oximeter on the *other* hand | 10 readings per volunteer at rest, 5 after exercise, 5 with a cold hand. Both devices stable for 20 s before reading. | ARMS ≤ 4 % and ≥ 90 % within ±3 % |
| 3 | **Joint angle** (elbow, knee) | goniometer, two raters, mean | for 0°, 45°, 90°: hold 5 s, 5 repetitions each after calibration; band angle vs the mean goniometer reading | max error ≤ 5° at each angle (roadmap M3), ≥ 95 % within ±5° |
| 4 | **Rep counting** | a human counting aloud | 10 reps × 3 sets, two tempos | every set within ±1 rep |
| 5 | **Temperature** | calibrated thermometer (axilla/oral) | 5 readings at rest | MAE ≤ 0.5 °C after the config offset is set (`hardware.temp_offset_c`) |
| 6 | **Falls** | simulated, supervised | per volunteer: 5 falls forward/back/side onto the mat, 10 ADLs (sit down hard, bend, jump, lie down) | sensitivity ≥ 95 %, < 1 false fall alarm / day in test 7 |
| 7 | **False alarms** | none (normal life) | wear 24–72 h doing normal things, ECG on, SpO2 clip optional. No deliberate tests. | report alerts/24 h by level and kind (critical target ≈ 0) |
| 8 | **Battery** | – | full charge, normal use, log `% vs time` for ≥ 30 min, then run to empty once | ≥ 24 h target (docs/03 predicts 17–20 h on 3 cells; 24 h needs 4 cells or Pi Zero 2 W) |
| 9 | **SMS path** | – | 5 deliveries to 2 phones with the SIM800L, weak signal and normal | 5/5 delivered, median latency recorded |
| 10 | **Comfort** | questionnaire | 24 h wear: sleep, shower guidance, electrode skin reaction | free text + 1–5 scores |

Limitations to state in the report: healthy volunteers cannot show accuracy at low SpO2; the guard in
`evaluate("spo2")` adds that caveat automatically when the reference never fell below 94 %.

## Capturing data
1. Run the logger on a laptop on the same network, one file per volunteer and test:
   `python -m senseband_edge.validation.logger ws://<band>:8080/ws/live V01_hr.csv 1800`
2. Write the reference readings in `validation/templates/*.csv` format
   (`subject,condition,t_s,device,reference`; `t_s` is seconds on a clock shared with the logger).
   For tests 1–2 pair them with `pair_by_time(device, reference, tol_s=30)`; for angles enter both values per row.
3. After the wear test: `curl "http://<band>:8080/api/history?kind=events" > events.json` and note the hours worn.
4. Battery: log `t_s,percent` from the band's `/api/status` every minute (`band.battery_pct` is the small band; read the
   main band's UPS/fuel gauge or a USB power meter).

## Analysis and report
```
python -m senseband_edge.validation --hr hr.csv --spo2 spo2.csv --angle angle.csv \
   --battery battery.csv --events events.json --hours 48 --out VALIDATION_REPORT.md
```
The report lists bias, 95 % limits of agreement (Bland–Altman), MAE, ARMS, r, share within tolerance, per-volunteer
MAE, the false-alarm rate, the battery projection, and **caveats** (few subjects, narrow range, one volunteer much
worse). Exit code 0 = every criterion passed.

## What to do with the result
- Fill the table in `README.md` ("Measured accuracy") with these numbers and the date; remove "not yet measured".
- Failing criteria are findings, not paperwork: adjust (e.g. SpO2 calibration constants in firmware, `activity.*`
  thresholds, `thresholds.*`), re-run, and keep every report.
- Retrain the models with the study's labelled recordings (`POST /api/record/start`) and re-validate. Only after the fall
  model passes on **real** falls may `models.fall.enabled` be set to true.
- Anything that reaches users needs regulatory advice first (India: CDSCO medical-device rules); do not describe SenseBand
  as diagnosing or treating anything.

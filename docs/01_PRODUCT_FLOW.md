# 01 – Product flow

## Users and roles
| Role | Can do |
|---|---|
| **Patient** | wear the band, run tests/exercises, see own data |
| **Caregiver / family** | see live data + alerts, get SMS/push, run the app for the patient |
| **Doctor / physio** | see all their patients, triage by alerts, view reports and ECG strips, edit exercise plans and targets, add notes |

One **patient profile** holds everything; the patient (or caregiver) invites others with a code/link.
Access can be revoked any time.

## 1. Onboarding (first time)
1. Welcome → sign up / log in (phone OTP or email). *Offline first-run must also work: create the
   profile locally on the band and sync it later.*
2. **Patient profile**: name, age, sex, height, weight, known conditions, medicines (esp. beta-blockers –
   they change heart rate), resting HR if known, emergency contacts (SMS numbers), doctor (optional invite).
3. **Pair device**: phone joins the band's Wi-Fi hotspot (or same Wi-Fi) → scan QR printed on the band →
   app shows "Main band ✓ / Small band ✓ / Sensors ✓".
4. **"What do you need SenseBand for?"**
   - 🩹 **Accident / fracture recovery**
   - ❤️ **Health monitoring**
   (A patient can have both programs over time; the home screen adapts to the active program.)

## 2. Health monitoring flow
1. **Purpose**: Elderly daily care · Post-hospital discharge · Athlete / sports · General check
2. **Duration**: 24 h · 48 h · 7 days · Custom · Continuous (no end)
3. **Wear guide** (step cards with pictures, per purpose):
   - Main band on waist, all components at the front (nothing on the back – comfortable lying down).
   - ECG: 3 electrodes on the chest (RA / LA / RL), leads clipped to the belt.
   - Small band: finger/ear clip for SpO2 (elderly, post-discharge, night use). Optional for athletes.
   - Athlete mode: tight elastic chest/waist strap, ECG leads taped; SpO2 clip off during play.
4. **Signal check** (must pass before start): ECG quality good · finger detected · IMU steady ·
   battery % · SIM signal · internet yes/no. Each item shows a fix tip if it fails.
5. **Learning period**: first 24–48 h the app says "Learning your normal" – personal baselines are
   being built; only absolute-safety alerts fire during this time.
6. **Live dashboard**
   - Big tiles: Heart rate (with **context label**, e.g. "Jogging – normal for activity"),
     SpO2, Temperature, Rhythm (regular / possibly irregular), Activity (lying, sitting, walking, running).
   - Status line: "All normal" / "Watch" / "Alert" (from the early-warning score).
   - Activity timeline for today, HR vs expected-HR chart, sleep summary (night).
   - Alerts list with acknowledge button; ECG strip saved for every event.
7. **End of program / daily**: report with trends, min/max/avg, events, ECG strips, sleep, activity →
   export PDF / share with doctor.

## 3. Accident / fracture recovery flow
1. **Where is the injury?** Arm or leg → joint (shoulder, elbow, wrist, hip, knee, ankle) → left/right.
2. **Injury details**: date of injury, fracture / surgery / sprain, cast removed date, doctor's
   restrictions (max allowed angle, weight-bearing), current pain (0–10).
3. **Wear guide for that joint** (picture + text; see table in 03_HARDWARE.md):
   main band on the segment **above** the joint, small band **below**. For shoulder/hip the main band
   stays on the waist as the trunk reference.
4. **Calibration**: hold the limb in the neutral position (straight) for 3 s → app confirms.
5. **Baseline assessment** (day 1, then weekly):
   - Active ROM for each movement of that joint (e.g. elbow flexion/extension, forearm rotation).
   - Pain score after the test.
   - Grip test (hand/wrist/elbow injuries): injured side vs healthy side.
6. **Plan** generated from the exercise library and the baseline (% of normal ROM, pain, days since
   injury). Shown as "Waiting for doctor approval" until the doctor approves/edits (patient may start
   the gentle stage-1 plan if no doctor is linked, with a clear notice).
7. **Daily session**
   1. Pre-check: quick ROM test (2 reps) + pain score.
   2. **Guided exercise**: live angle gauge with the target zone, rep counter, tempo cue
      (too fast warning), "hold" timer, sets/rest timer. Warn if the doctor's max angle is exceeded.
      Stop button and automatic stop if pain ≥ 7.
   3. Post-check: ROM test again + pain score (shows warm-up gain and fatigue).
8. **Progress**: ROM per day chart with target line, % of normal, grip trend, adherence (sessions done),
   **estimated recovery date** (curve fit, with confidence band – shown after ≥ 5 sessions).
9. **Doctor view**: same charts + raw session details; can change targets, exercises, restrictions;
   notes appear in the patient app.

## 4. Alerts – what the user experiences
| Level | Example | What happens |
|---|---|---|
| Info | sensor off, battery low, sync pending | app notice only |
| Warning | HR unusual for this context, SpO2 90–92 %, possible irregular rhythm, fever | app + push to caregiver; SMS if offline |
| Critical | fall + no movement, HR < 40 or very high at rest, SpO2 < 88 %, HR not recovering after exertion + abnormal ECG | band beeps for 30 s with **"I'm OK" button** to cancel; if not cancelled → SMS to all contacts + push + doctor dashboard |

## 5. Screens list (for the app)
Onboarding: Welcome · Login · Patient profile · Pair device · Choose program
Monitoring: Purpose · Duration · Wear guide · Signal check · Live dashboard · Alerts · Report
Recovery: Injury location · Injury details · Wear guide · Calibrate · Assessment · Plan · Exercise session · Progress
Shared: Patient profile & care team · Settings (contacts, units, thresholds view) · Device status
Doctor: Patient list (sorted by alerts) · Patient overview · Edit plan · Notes
Every screen must work on a phone (primary) and a laptop.

"""Context engine: turns sensor bundles into vitals, context and alerts.

Rule-based fallback for models M1-M5/M10 (ML models replace pieces in Milestone 5). Driven entirely
by the data clock (frame timestamps) so tests on the simulator are fast and deterministic.

Safety rules enforced here:
  * a vital alert is never raised from a signal flagged poor (lead-off, noisy, finger-off, motion):
    those produce a "check sensor" notice instead;
  * wording never diagnoses ("possible irregular rhythm - share with your doctor");
  * critical alerts always go to SMS after the "I'm OK" window.
"""
from __future__ import annotations

import json
import time
from collections import Counter, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ..alerts.ews import ews_score
from ..alerts.manager import Alert, AlertManager
from ..alerts.sms import SmsGateway, send_with_retries
from ..config import Settings
from ..hw.io import BandIO
from ..hw.source import Bundle
from ..models import registry as mreg
from ..models.personal import PersonalHr
from ..pipeline.activity import ActivityClassifier, ActivityResult
from ..pipeline.blelink import SmallBandParser
from ..pipeline.ecg import EcgAnalyzer, EcgResult
from ..pipeline.fall import FallDetector
from ..power import PowerManager
from ..record import Recorder
from ..rehab.engine import RehabEngine
from ..storage.db import Store
from ..storage.raw import RawStore


class Sustain:
    """Seconds a condition has been continuously true (resets on any false / missing data)."""

    def __init__(self) -> None:
        self.since: float | None = None

    def update(self, cond: bool, now: float) -> float:
        if not cond:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return now - self.since


class Engine:
    def __init__(self, cfg: Settings, store: Store | None, sms: SmsGateway, io: BandIO,
                 base_ms: int | None = None, sleep: Callable[[float], None] = time.sleep,
                 emit: Callable[[dict[str, Any]], None] | None = None):
        self.cfg, self.store, self.sms, self.io = cfg, store, sms, io
        self.sleep, self.emit = sleep, emit
        self.base_ms = base_ms if base_ms is not None else int(time.time() * 1000)
        self.hw = cfg.hardware
        self.models = mreg.ModelRegistry(cfg.models)
        mc = cfg.models
        self.ecg = EcgAnalyzer(
            self.hw.ecg_hz, cfg.quality,
            lambda x: mreg.ecg_quality_probs(self.models, x, self.hw.ecg_hz), mc.ecg_quality.min_prob)
        self.act = ActivityClassifier(50, cfg.activity, lambda w: mreg.activity_probs(self.models, w),
                                      mc.activity.min_prob)
        self.fall = FallDetector(cfg.fall)
        self._acc_ring: deque[tuple[float, np.ndarray]] = deque(maxlen=50 * 8)
        self._fall_checked: float | None = None
        self.personal = PersonalHr()
        self.power = PowerManager(cfg.power)
        self.ecg_control: Callable[[bool], None] = lambda on: None   # set by the service: SDN pin / simulator
        self.recorder = Recorder(Path(cfg.storage.recordings_dir), cfg.storage.max_recording_s)
        self.raw = RawStore(store, cfg.storage.raw_days) if (store is not None and cfg.storage.keep_raw) else None
        self._personal_saved = 0.0
        self._load5: deque[tuple[float, float]] = deque(maxlen=400)
        self.learning_start_ms: int | None = None
        if store is not None:
            saved = store.state_get("personal_hr")
            if saved:
                try:
                    self.personal = PersonalHr.from_state(json.loads(saved))
                except (ValueError, KeyError):
                    pass
        self.band = SmallBandParser(cfg.quality)
        self.alerts = AlertManager(cfg.alerts, self._on_alert_change)
        self.program_id: str | None = None
        self.rehab = RehabEngine(cfg, store, self._emit, self.epoch_ms, lambda: self.program_id)
        cal = np.asarray(cfg.hardware.grip_cal, dtype=float)
        self._grip_v, self._grip_kg = cal[:, 0], cal[:, 1]
        self.sms_dispatch: Callable[[Alert], None] = self._send_sms_sync
        self.beta_blocker = cfg.patient.beta_blocker   # follows the patient's medicines
        self.contacts: list[str] = []          # emergency contacts from the patient profile
        self.now = 0.0
        self._next_tick = 1.0
        self.temp_c: float | None = None
        self.temp_t: float | None = None
        self.ecg_res = EcgResult()
        self.vitals: dict[str, Any] = {}
        self.last_exertion_t = -1e9
        self.baseline_hr: float | None = cfg.patient.resting_hr
        self._baseline_n = 0
        self.s: dict[str, Sustain] = {k: Sustain() for k in (
            "hr_high", "hr_crit", "hr_low", "hr_active", "spo2_warn", "spo2_crit", "irregular",
            "fever", "ews", "ecg_bad", "ppg_bad", "no_pulse")}
        self._spo2_hist: deque[tuple[float, float]] = deque(maxlen=400)
        self._desat_events: deque[float] = deque(maxlen=50)
        self._in_dip = False
        self._ews = 0
        self.stats: Counter[str] = Counter()
        self._minute: dict[str, Any] = {}
        self._minute_idx: int | None = None
        self.mock_events: list[dict[str, Any]] = []     # messages when no emit callback

    # ---- clock helpers ----------------------------------------------------------------------
    def epoch_ms(self, t: float) -> int:
        return self.base_ms + int(t * 1000)

    # ---- main entry: one frame --------------------------------------------------------------
    def process(self, b: Bundle) -> None:
        f = b.frame
        now = self.now = f.t1
        quats: list[tuple[float, float, float, float]] = []
        for line in b.ble_lines:
            if self.band.feed(line, now) == "Q" and self.band.state.quat is not None:
                quats.append(self.band.state.quat)
        if quats and self.rehab.mode != "idle":
            ref = f.imu["limb_ref"]
            for i, q in enumerate(quats):               # spread over the frame: the band's clock differs
                t = f.t0 + (i + 0.5) * (f.t1 - f.t0) / len(quats)
                if len(ref.quat_wxyz):
                    k = min(max(int((t - ref.t0) * ref.fs), 0), len(ref.quat_wxyz) - 1)
                    self.rehab.on_pair(t, tuple(ref.quat_wxyz[k]), q)
        self.rehab.tick(now)
        if f.grip is not None and self.rehab.mode == "grip":
            self.rehab.on_grip(f.grip, now, lambda v: np.interp(v, self._grip_v, self._grip_kg))
        self.ecg.add_chunk(now, f.ecg.mv, f.ecg.lead_off)
        w = f.imu["waist"]
        self.act.add(w.acc_g, now)
        for i in range(len(w.acc_g)):
            self._acc_ring.append((w.t0 + i / w.fs, w.acc_g[i]))
        self._fall_model_check(now)
        for i, m in enumerate(np.linalg.norm(w.acc_g, axis=1)):
            ev = self.fall.update(float(m), w.t0 + i / w.fs)
            if ev == "fall":
                self.raise_alert("fall", "critical",
                                 "Possible fall - no movement detected. Press the button if OK.",
                                 w.t0 + i / w.fs)
            elif ev == "impact":
                self.stats["impacts"] += 1
        if self.recorder.active and not self.recorder.add(f):
            self.stats["recording_capped"] += 1
        if self.raw is not None:
            self.raw.add(f, self.epoch_ms(now))
        for s in f.temp:
            self.temp_c, self.temp_t = s.celsius + self.hw.temp_offset_c, s.t
        self._emit({"t": "ecg", "ts": self.epoch_ms(now), "fs": f.ecg.fs,
                    "samples": [round(float(v), 3) for v in f.ecg.mv]})
        for a in self.alerts.tick(now):
            self.sms_dispatch(a)
        self._update_io(now)
        if now >= self._next_tick - 1e-9:
            self._next_tick += 1.0
            self._tick(now)

    def _power_step(self, now: float) -> None:
        band = self.band.state
        ppg_hr = band.hr if band.fresh(now) and band.hr_valid else None
        alert_open = bool(self.alerts.pending_countdown) or any(
            a.level != "info" and now - a.t < 600 for a in self.alerts.alerts)
        was_on = not self.power.paused
        on = self.power.update(now, self.act.result.activity, ppg_hr, alert_open, self.cfg.thresholds.hr_low)
        if on == was_on:                                # no change: stay paused (keep the front-end off) or stay active
            if not on:
                self.ecg_control(False)
            return
        self.ecg_control(on)
        if on:
            self.ecg.reset()                            # the gap is not signal: relearn for ~4 s

    def _fall_model_check(self, now: float) -> None:
        """Model M2 (when enabled) can veto a rule trigger 2 s after impact, before the stillness wait."""
        t = self.fall.trigger_t
        if t is None:
            self._fall_checked = None
            return
        if self._fall_checked == t or now - t < 2.0:
            return
        self._fall_checked = t
        rows = [(tt, a) for tt, a in self._acc_ring if t - 2.0 <= tt <= t + 2.0]
        if len(rows) < 100:
            return
        p = mreg.fall_prob(self.models, np.stack([a for _, a in rows]))
        if p is not None and p < self.cfg.models.fall.veto_below:
            self.fall.cancel()
            self.stats["fall_vetoed"] += 1

    # ---- 1 Hz decision step -----------------------------------------------------------------
    def _tick(self, now: float) -> None:
        th, s = self.cfg.thresholds, self.s
        self._power_step(now)
        self.ecg_res = ecg = EcgResult(quality="paused") if self.power.paused else self.ecg.analyze(now)
        act: ActivityResult = self.act.result
        band = self.band.state
        fresh = band.fresh(now)
        if act.activity == "running":
            self.last_exertion_t = now
        in_recovery = now - self.last_exertion_t < th.recovery_allowance_s

        # ---- vitals with quality gating (poor signal => None) ----
        hr: float | None = None
        hr_src: str | None = None
        if ecg.quality == "good" and ecg.hr:
            hr, hr_src = ecg.hr, "ecg"
        elif fresh and band.hr_valid and band.hr:
            hr, hr_src = band.hr, "ppg"
        spo2 = band.spo2 if fresh and band.spo2_valid else None
        temp = self.temp_c if self.temp_t is not None and now - self.temp_t < 30 else None
        ppg_q = ("none" if not fresh else "finger_off" if not band.finger
                 else "good" if band.hr_valid else "poor")
        rhythm, irregular = self._rhythm(ecg, now)

        # personal baseline: resting HR learned over calm periods
        if hr_src == "ecg" and act.resting and not in_recovery and hr is not None and hr < 100:
            self._baseline_n += 1
            if self.baseline_hr is None:
                self.baseline_hr = hr
            elif self._baseline_n > 60:
                self.baseline_hr = 0.995 * self.baseline_hr + 0.005 * hr

        # expected HR for this context: model M5 + personal correction, else the rule table
        lo, hi = getattr(self.cfg.expected_hr, act.activity, None) or (60.0, 100.0)
        scale = self.cfg.expected_hr.beta_blocker_scale if self.beta_blocker else 1.0
        hr_expected = (self.baseline_hr if act.resting and self.baseline_hr
                       else (lo + hi) / 2 * scale)
        learning = (self.learning_start_ms is not None
                    and self.epoch_ms(now) - self.learning_start_ms < self.cfg.models.learning_period_h * 3600_000)
        lo_r, hi_r = lo * scale, hi * scale
        modelled = self._expected_hr(now, act, hr, hr_src, in_recovery, temp, learning)
        if modelled is not None:
            hr_expected, (lo_r, hi_r) = modelled

        # exercise HR is expected to be high, so only resting HR enters the early-warning score
        self._ews = ews_score(self.cfg.ews, hr if act.resting else None, spo2, temp)

        # ---- alert rules ----
        cx = {"activity": act.activity, "hr": hr, "hr_src": hr_src, "spo2": spo2, "temp": temp}

        # heart rate high at rest (not right after exertion)
        hot = hr is not None and act.resting and not in_recovery and hr > th.hr_high_rest
        if s["hr_high"].update(hot, now) >= th.hr_rest_sustain_s:
            self.raise_alert(
                "hr_high_rest", "warning",
                f"Heart rate {hr:.0f} bpm while resting - higher than expected. Rest and recheck; "
                "contact your doctor if it continues.", now, cx)
        crit = hr is not None and act.resting and not in_recovery and hr > th.hr_critical_rest
        if s["hr_crit"].update(crit, now) >= th.hr_critical_sustain_s:
            self.raise_alert("hr_critical_rest", "critical",
                             f"Heart rate very high ({hr:.0f} bpm) at rest. "
                             "Press the button if OK.", now, cx)
        low_limit = th.hr_low_sleep if act.activity == "sleeping" else th.hr_low
        low = hr is not None and act.resting and hr < low_limit
        if s["hr_low"].update(low, now) >= th.hr_low_sustain_s:
            self.raise_alert("hr_low", "critical",
                             f"Heart rate very low ({hr:.0f} bpm). Press the button if OK.",
                             now, cx)
        busy = hr is not None and not act.resting and hr > th.hr_high_active
        if s["hr_active"].update(busy, now) >= th.hr_active_sustain_s:
            self.raise_alert("hr_high_active", "warning",
                             f"Heart rate {hr:.0f} bpm during activity - consider slowing down.",
                             now, cx)

        # SpO2 (valid PPG only)
        if s["spo2_crit"].update(spo2 is not None and spo2 < th.spo2_crit, now) \
                >= th.spo2_crit_sustain_s:
            self.raise_alert("spo2_critical", "critical",
                             f"Blood oxygen very low ({spo2:.0f} %). Press the button if OK.",
                             now, cx)
        if s["spo2_warn"].update(spo2 is not None and spo2 < th.spo2_warn, now) \
                >= th.spo2_warn_sustain_s:
            self.raise_alert("spo2_low", "warning",
                             f"Blood oxygen {spo2:.0f} % - lower than expected. Sit upright, "
                             "breathe slowly and recheck.", now, cx)
        self._desat_rule(now, spo2, act, cx)

        # rhythm (ECG good, at rest)
        irr = ecg.quality == "good" and act.resting and irregular
        if s["irregular"].update(irr, now) >= th.rr_irregular_sustain_s:
            self.raise_alert("irregular_rhythm", "warning",
                             "Possible irregular rhythm detected - please share this with your "
                             "doctor. This is not a diagnosis.", now, cx)

        # fever / infection pattern
        fever = (temp is not None and temp > th.temp_high and hr is not None and act.resting
                 and self.baseline_hr is not None and hr > self.baseline_hr + th.temp_hr_margin)
        if s["fever"].update(bool(fever), now) >= th.temp_sustain_s:
            self.raise_alert("fever_pattern", "warning",
                             "Temperature and resting heart rate are both raised - possible "
                             "fever or infection. Please check with a thermometer.", now, cx)

        # early-warning score
        if s["ews"].update(self._ews >= self.cfg.ews.alert_at, now) >= 30:
            self.raise_alert("early_warning", "warning",
                             "Several vital signs are outside their usual range.", now, cx)

        # collapse: contact OK but no heartbeat, no movement, no pulse on the finger
        no_pulse = (ecg.quality == "flat" and ecg.flat_s >= 0 and not act.moving
                    and not (fresh and band.hr_valid))
        if s["no_pulse"].update(no_pulse, now) >= th.no_pulse_s:
            self.raise_alert("collapse", "critical",
                             "No heartbeat detected and no movement - possible collapse. "
                             "Press the button if OK.", now, cx)

        # poor signal => "check sensor" notice (never a vital alert)
        flat_bad = ecg.quality == "flat" and (act.moving or (fresh and band.hr_valid))
        ecg_bad = ecg.quality in ("lead_off", "noisy") or flat_bad      # "paused" is intentional, never a fault
        if s["ecg_bad"].update(ecg_bad, now) >= th.check_sensor_s:
            why = {"lead_off": "ECG electrode not touching the skin - check electrodes and cable.",
                   "noisy": "ECG signal is noisy - sit still and check electrode contact."
                   }.get(ecg.quality, "No ECG signal - check electrodes and cable.")
            self.raise_alert("check_sensor_ecg", "info", why, now, cx)
        if s["ppg_bad"].update(fresh and not band.finger, now) >= th.check_sensor_s:
            self.raise_alert("check_sensor_ppg", "info",
                             "SpO2 clip is not on the finger - check the clip.", now, cx)

        # ---- publish + aggregate ----
        self.vitals = {
            "t": "vitals", "ts": self.epoch_ms(now), "hr": hr, "hr_src": hr_src,
            "hr_expected": round(float(hr_expected), 1),
            "hr_range": [round(lo_r), round(hi_r)], "context": act.activity, "learning": learning,
            "posture": act.posture, "intensity": round(act.intensity, 2), "spo2": spo2,
            "temp": None if temp is None else round(temp, 2), "rhythm": rhythm,
            "quality": {"ecg": ecg.quality, "ppg": ppg_q}, "ews": self._ews,
            "status": ("alert" if self.alerts.pending_countdown or
                       self._ews >= self.cfg.ews.alert_at else
                       "watch" if self._ews >= self.cfg.ews.watch_at else "normal"),
        }
        self._emit(self.vitals)
        self._aggregate(now, hr, hr_src, band.hr if band.hr_valid else None, spo2, temp, act,
                        rhythm, ecg.quality, ppg_q)

    def _rhythm(self, ecg: EcgResult, now: float) -> tuple[str, bool]:
        """(label for the app, is_irregular). Model M4 when available, else RR variability (rule fallback)."""
        th, mc = self.cfg.thresholds, self.cfg.models.rhythm
        rr = self.ecg.recent_rr(now, 30.0)
        if ecg.quality == "good" and len(rr) >= mc.min_beats:
            probs = mreg.rhythm_probs(self.models, rr)
            if probs:
                if probs.get("af", 0.0) >= mc.af_prob:
                    return "possible_irregular", True
                if probs.get("noisy", 0.0) >= 0.5:
                    return "unknown", False
                return "regular", False
        if ecg.rr_cv is None:
            return "unknown", False
        bad = ecg.rr_cv > th.rr_irregularity
        return ("possible_irregular" if bad else "regular"), bad

    def _expected_hr(self, now: float, act: ActivityResult, hr: float | None, hr_src: str | None,
                     in_recovery: bool, temp: float | None, learning: bool
                     ) -> tuple[float, tuple[float, float]] | None:
        if self.models.get("expected_hr") is None:
            return None
        self._load5.append((now, act.intensity))
        recent = [i for t, i in self._load5 if now - t <= 300]
        load5 = float(np.mean(recent)) if recent else act.intensity
        lt = time.localtime(self.epoch_ms(now) / 1000)
        pred = mreg.expected_hr_value(self.models, act.activity, act.intensity, act.cadence_hz, load5,
                                      lt.tm_hour + lt.tm_min / 60, temp, self.beta_blocker)
        if pred is None:
            return None
        mc = self.cfg.models.expected_hr
        exp = self.personal.correct(pred)
        if hr_src == "ecg" and hr is not None and not in_recovery and int(now) % 10 == 0:
            self.personal.update(pred, hr)                    # learn THIS person (calm, good-quality windows)
        if self.store is not None and now - self._personal_saved > 600:
            self._personal_saved = now
            self.store.state_set("personal_hr", json.dumps(self.personal.state()))
        sd = float(self.models.metrics.get("m5_expected_hr", {}).get("residual_sd_bpm", 10.0))
        half = max(mc.min_band_bpm, 2.0 * sd) * (mc.learning_band_scale if learning else 1.0)
        return exp, (exp - half, exp + half)

    def _desat_rule(self, now: float, spo2: float | None, act: ActivityResult,
                    cx: dict[str, Any]) -> None:
        th = self.cfg.thresholds
        if spo2 is None or act.activity not in ("lying", "sleeping"):
            self._in_dip = False
            return
        self._spo2_hist.append((now, spo2))
        vals = [v for t, v in self._spo2_hist if now - t <= 180]
        if len(vals) < 20:
            return
        base = float(np.percentile(vals, 90))
        if not self._in_dip and spo2 <= base - th.desat_drop_pct:
            self._in_dip = True
            self._desat_events.append(now)
        elif self._in_dip and spo2 >= base - th.desat_drop_pct / 2:
            self._in_dip = False
        n = sum(1 for t in self._desat_events if now - t <= th.desat_window_s)
        if n >= th.desat_events_warn:
            self.raise_alert("sleep_desats", "warning",
                             f"Breathing dips during sleep ({n} in {th.desat_window_s / 60:.0f} "
                             "min) - discuss with your doctor.", now, {**cx, "events": n})

    # ---- alerts: raise / user actions / SMS -------------------------------------------------
    def raise_alert(self, kind: str, level: str, message: str, now: float,
                    context: dict[str, Any] | None = None,
                    countdown_s: float | None = None) -> Alert | None:
        a = self.alerts.raise_alert(kind, level, message, now, context or self._ctx(), countdown_s)
        if a is not None:
            self.stats[f"alert_{level}"] += 1
        return a

    def _ctx(self) -> dict[str, Any]:
        v = self.vitals
        return {k: v.get(k) for k in ("hr", "hr_src", "spo2", "temp", "context")}

    def i_am_ok(self, by: str = "button") -> list[Alert]:
        """'I'm OK' (button press or app): cancels critical alerts still in their countdown."""
        out = self.alerts.cancel(self.now, by)
        self._update_io(self.now)
        return out

    def sos(self) -> Alert | None:
        """Long-press SOS: critical alert that is sent without waiting (sos_countdown_s)."""
        a = self.alerts.raise_alert("sos", "critical", "SOS button pressed - needs help now.",
                                    self.now, self._ctx(), self.cfg.alerts.sos_countdown_s)
        if a is not None:
            for d in self.alerts.tick(self.now):
                self.sms_dispatch(d)
        return a

    def _send_sms_sync(self, a: Alert) -> None:
        p = self.cfg.patient
        text = f"SenseBand {a.level.upper()} [{p.name}]: {a.message}"
        contacts = self.contacts or self.cfg.sms.contacts
        res = send_with_retries(self.sms, contacts, text, self.cfg.sms.retries,
                                self.cfg.sms.retry_delay_s, self.sleep)
        self.alerts.sms_done(a, len(res.delivered))

    def _on_alert_change(self, a: Alert, why: str) -> None:
        if self.store is not None:
            self._persist(a, why)
        self._emit({"t": "event", "why": why, **self.public(a)})

    def public(self, a: Alert) -> dict[str, Any]:
        """Alert for the app: includes seconds left in the "I'm OK" window (app ticks locally)."""
        d = a.public()
        d["countdown_left_s"] = (max(0.0, round(a.countdown_ends - self.now, 1))
                                 if a.status == "active" and a.countdown_ends is not None else None)
        return d

    def _persist(self, a: Alert, why: str) -> None:
        assert self.store is not None
        if why == "raised":
            self.store.upsert("events", {
                "id": a.id, "patient_id": self.cfg.patient.id, "ts": self.epoch_ms(a.t),
                "level": a.level, "kind": a.kind, "message": a.message,
                "context_json": a.context, "sms_sent": 0, "status": a.status})
            strip = self.ecg.strip(self.cfg.alerts.ecg_strip_s)
            if len(strip):
                self.store.upsert("ecg_strips", {
                    "event_id": a.id, "ts": self.epoch_ms(a.t), "fs": self.hw.ecg_hz,
                    "samples": strip.tobytes()})
            return
        fields: dict[str, Any] = {"status": a.status, "sms_sent": a.sms_sent}
        if a.acknowledged_by:
            fields.update(acknowledged_by=a.acknowledged_by, ack_ts=self.epoch_ms(self.now))
        if why == "cancelled":
            fields["cancelled_ts"] = self.epoch_ms(self.now)
        self.store.update("events", a.id, fields)

    # ---- buzzer / LED -----------------------------------------------------------------------
    def _update_io(self, now: float) -> None:
        pending = self.alerts.pending_countdown
        if pending:
            on_s, off_s = self.cfg.alerts.buzzer_pattern_s
            self.io.buzzer((now % (on_s + off_s)) < on_s)
            self.io.led("fast")
        else:
            self.io.buzzer(False)
            recent_warn = any(a.level == "warning" and a.acknowledged_by is None
                              and now - a.t < 600 for a in self.alerts.alerts)
            self.io.led("slow" if recent_warn else "off")

    # ---- 1-minute aggregates (vitals_minute) ------------------------------------------------
    def _aggregate(self, now: float, hr: float | None, src: str | None, hr_ppg: float | None,
                   spo2: float | None, temp: float | None, act: ActivityResult, rhythm: str,
                   ecg_q: str, ppg_q: str) -> None:
        ms = self.epoch_ms(now)
        idx = ms // 60000
        if self._minute_idx is not None and idx != self._minute_idx:
            self._flush_minute()
        self._minute_idx = idx
        m = self._minute
        if src == "ecg" and hr is not None:
            m.setdefault("hr_ecg", []).append(hr)
        if hr_ppg is not None:
            m.setdefault("hr_ppg", []).append(hr_ppg)
        if spo2 is not None:
            m.setdefault("spo2", []).append(spo2)
        if temp is not None:
            m.setdefault("temp", []).append(temp)
        m.setdefault("activity", []).append(act.activity)
        m.setdefault("posture", []).append(act.posture)
        m.setdefault("intensity", []).append(act.intensity)
        m.setdefault("rhythm", []).append(rhythm)
        m.setdefault("ews", []).append(self._ews)
        m.setdefault("hr_expected", []).append(self.vitals.get("hr_expected"))
        flags = m.setdefault("flags", set())
        if ecg_q != "good":
            flags.add(f"ecg_{ecg_q}")
        if ppg_q != "good":
            flags.add(f"ppg_{ppg_q}")

    def _flush_minute(self) -> None:
        m, idx = self._minute, self._minute_idx
        self._minute = {}
        if self.store is None or not m or idx is None:
            return
        mean = lambda k: round(float(np.mean(m[k])), 1) if m.get(k) else None  # noqa: E731
        mode = lambda k: Counter(m[k]).most_common(1)[0][0] if m.get(k) else None  # noqa: E731
        self.store.upsert("vitals_minute", {
            "patient_id": self.cfg.patient.id, "minute_ts": idx * 60000,
            "hr_ecg": mean("hr_ecg"), "hr_ppg": mean("hr_ppg"), "hr_expected": mean("hr_expected"),
            "spo2_min": min(m["spo2"]) if m.get("spo2") else None, "spo2_avg": mean("spo2"),
            "temp": mean("temp"), "activity": mode("activity"), "intensity": mean("intensity"),
            "posture": mode("posture"), "rhythm": mode("rhythm"),
            "quality_flags": sorted(m.get("flags", [])),
            "ews": max(m["ews"]) if m.get("ews") else 0})

    def flush(self) -> None:
        self._flush_minute()
        if self.raw is not None:
            self.raw.flush()

    # ---- views for the API ------------------------------------------------------------------
    def _emit(self, msg: dict[str, Any]) -> None:
        if self.emit is not None:
            self.emit(msg)

    def signal_check(self) -> dict[str, Any]:
        """Live quality of each sensor with a plain-language fix tip."""
        q, band, now = self.ecg_res.quality, self.band.state, self.now
        fresh = band.fresh(now)
        ecg_tip = {"good": "ECG is good.", "collecting": "Hold still - measuring...",
                   "lead_off": "Electrode not touching. Moisten pads, press firmly, check cable.",
                   "noisy": "Signal noisy. Sit still, relax shoulders, re-seat the electrodes.",
                   "flat": "No signal. Check electrodes and cable.",
                   "paused": "ECG is paused while you sleep to save battery; it resumes on any change."}[q]
        if not fresh:
            ppg, ppg_tip = "none", "Small band not connected. Switch it on and keep it close."
        elif not band.finger:
            ppg, ppg_tip = "finger_off", "Put the clip on the finger pad, snug but not tight."
        elif not band.hr_valid:
            ppg, ppg_tip = "poor", "Keep the hand still and warm; check the clip position."
        else:
            ppg, ppg_tip = "good", "SpO2 sensor is good."
        act = self.act.result
        return {
            "ecg": {"quality": q, "tip": ecg_tip},
            "ppg": {"quality": ppg, "tip": ppg_tip},
            "imu": {"quality": "good" if act.activity != "unknown" else "collecting",
                    "activity": act.activity, "tip": "Wear the main band snugly."},
            "temp": {"quality": "good" if self.temp_c is not None else "none",
                     "tip": "Skin temperature sensor needs skin contact."},
            "ready": q == "good" and ppg == "good",
        }

    def band_status(self) -> dict[str, Any]:
        b = self.band.state
        return {"connected": b.fresh(self.now), "battery_pct": b.battery_pct,
                "last_seen": b.last_seen, "finger": b.finger}

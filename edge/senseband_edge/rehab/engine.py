"""RehabEngine: calibration, ROM / grip / pain assessments and guided exercise sessions.

Driven by the data clock: the Engine feeds it (reference quaternion, moving quaternion) pairs at the
small band's Q rate (10 Hz idle, 50 Hz during tests/exercises) and grip chunks on demand.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np

from ..config import Settings
from ..hw.frames import GripChunk
from ..storage.db import Store
from . import library
from .joints import MOVEMENTS, JointAngle, movements_of
from .reps import Rep, RepCounter, nominal_duration


class RehabError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class RehabEngine:
    def __init__(self, cfg: Settings, store: Store | None, emit: Callable[[dict[str, Any]], None],
                 epoch_ms: Callable[[float], int], program_id: Callable[[], str | None]):
        self.cfg, self.c = cfg, cfg.rehab
        self.store, self.emit, self.epoch_ms, self.program_id = store, emit, epoch_ms, program_id
        self.band_command: Callable[[str], None] = lambda text: None
        self.grip_hook: Callable[[bool], None] = lambda active: None
        self.mode = "idle"                      # idle | calibrating | rom | exercise | grip
        self.now = 0.0
        self.angles: dict[str, JointAngle] = {}
        self.calibrated_at: dict[str, float] = {}      # wall-clock ms
        self.last_result: dict[str, Any] | None = None
        self.last_angle: float | None = None
        self.last_off_axis = 0.0
        self.movement: str | None = None
        self._cal: dict[str, Any] = {}
        self._rom: dict[str, Any] = {}
        self._ex: dict[str, Any] = {}
        self._grip: dict[str, Any] = {}
        self._n = 0
        self._started = 0.0
        self._load_calibrations()

    # ---- calibration -----------------------------------------------------------------------
    def _load_calibrations(self) -> None:
        if self.store is None:
            return
        for r in self.store.list("calibrations", order="ts", limit=500):
            q = (r.get("offsets_json") or {}).get("q")
            if q and (time.time() * 1000 - r["ts"]) < self.c.calibration_max_age_h * 3600_000:
                from .joints import to_rot
                self.angles[r["joint"]] = JointAngle(to_rot(q))
                self.calibrated_at[r["joint"]] = r["ts"]

    def is_calibrated(self, joint: str) -> bool:
        return joint in self.angles

    def start_calibration(self, joint: str) -> dict[str, Any]:
        if joint not in {m.joint for m in MOVEMENTS.values()}:
            raise RehabError("unknown_joint", joint)
        self._require_idle()
        self.mode = "calibrating"
        self._started = self.now
        self._cal = {"joint": joint, "t0": None, "pairs": []}
        self.band_command(f"RATE,{25}")
        self.last_result = None
        return self.state()

    def _finish_calibration(self) -> None:
        j, pairs = self._cal["joint"], self._cal["pairs"]
        self.mode = "idle"
        self.band_command(f"RATE,{self.c.band_rate_idle_hz}")
        try:
            neutral, wobble = JointAngle.calibrate(pairs, self.c.calibration_steady_deg)
        except ValueError as e:
            self.last_result = {"kind": "calibration", "joint": j, "ok": False, "error": "not_steady",
                                "detail": str(e)}
            return
        self.angles[j] = JointAngle(neutral)
        ts = self.epoch_ms(self.now)
        self.calibrated_at[j] = ts
        w, x, y, z = (float(v) for v in (neutral.as_quat()[3], *neutral.as_quat()[:3]))
        if self.store is not None:
            self.store.upsert("calibrations", {
                "program_id": self.program_id(), "ts": ts, "joint": j,
                "offsets_json": {"q": [w, x, y, z], "wobble_deg": round(wobble, 2)}})
        self.last_result = {"kind": "calibration", "joint": j, "ok": True,
                            "wobble_deg": round(wobble, 2)}

    # ---- assessments ------------------------------------------------------------------------
    def start_rom(self, movement: str, phase: str = "baseline", side: str | None = None) -> dict:
        m = self._movement(movement)
        self._require_idle()
        if not self.is_calibrated(m.joint):
            raise RehabError("not_calibrated", f"calibrate the {m.joint} first")
        self.mode, self.movement = "rom", movement
        self._rom = {"movement": movement, "phase": phase, "side": side, "min": None, "max": None,
                     "t0": self.now}
        self.band_command(f"RATE,{self.c.band_rate_exercise_hz}")
        self.last_result = None
        return self.state()

    def stop_rom(self) -> dict[str, Any]:
        if self.mode != "rom":
            raise RehabError("not_running", "no ROM test is running")
        r = self._rom
        self.mode, self.movement = "idle", None
        self.band_command(f"RATE,{self.c.band_rate_idle_hz}")
        lo, hi = r["min"], r["max"]
        if lo is None:
            raise RehabError("no_data", "no movement data received")
        limit = self._limits().get(r["movement"])
        res = {"kind": "rom", "movement": r["movement"], "side": r["side"], "phase": r["phase"],
               "min_angle": round(lo, 1), "max_angle": round(hi, 1), "rom": round(hi - lo, 1),
               "normal_pct": round(100 * (hi - lo) / self.c.normal_rom[r["movement"]], 1),
               "over_limit": limit is not None and hi > limit, "limit_angle": limit,
               "off_axis_deg": round(self.last_off_axis, 1)}
        self._save_assessment(res)
        self.last_result = res
        return res

    def start_grip(self, side: str) -> dict[str, Any]:
        self._require_idle()
        self.mode = "grip"
        self._started = self.now
        self._grip = {"side": side, "t0": None, "kg": []}
        self.grip_hook(True)
        self.last_result = None
        return self.state()

    def on_grip(self, g: GripChunk, now: float, volts_to_kg: Callable[[np.ndarray], np.ndarray]) -> None:
        if self.mode != "grip":
            return
        gs = self._grip
        if gs["t0"] is None:
            gs["t0"] = g.t0
        gs["kg"].extend(volts_to_kg(g.volts).tolist())
        if now - gs["t0"] >= self.c.grip_test_s:
            self._finish_grip()

    def _finish_grip(self) -> None:
        gs = self._grip
        self.mode = "idle"
        self.grip_hook(False)
        kg = np.asarray(gs["kg"])
        if len(kg) == 0 or float(kg.max()) < 0.5:
            self.last_result = {"kind": "grip", "side": gs["side"], "ok": False, "error": "no_squeeze"}
            return
        top = kg[kg >= 0.5 * kg.max()]
        res = {"kind": "grip", "side": gs["side"], "ok": True, "grip_peak": round(float(kg.max()), 1),
               "grip_mean": round(float(top.mean()), 1), "unit": "kg"}
        self._save_assessment({**res, "phase": "baseline"})
        self.last_result = res

    def record_pain(self, score: int, phase: str) -> dict[str, Any]:
        if not 0 <= score <= 10:
            raise RehabError("bad_score", "pain must be 0 to 10")
        res = {"kind": "pain", "pain_0_10": score, "phase": phase}
        self._save_assessment(res)
        stopped = False
        if self.mode == "exercise" and score >= self.c.pain_stop:
            self.stop_exercise("pain", pain_after=score)
            stopped = True
        return {**res, "stopped_exercise": stopped}

    def _save_assessment(self, r: dict[str, Any]) -> None:
        if self.store is None:
            return
        row = {"program_id": self.program_id(), "ts": self.epoch_ms(self.now), "kind": r["kind"],
               "phase": r.get("phase", "baseline"), "movement": r.get("movement"), "side": r.get("side")}
        for k in ("min_angle", "max_angle", "rom", "grip_peak", "grip_mean", "unit", "pain_0_10"):
            if k in r:
                row[k] = r[k]
        self.store.upsert("assessments", row)

    # ---- guided exercise --------------------------------------------------------------------
    def start_exercise(self, exercise_id: str, item: dict[str, Any] | None = None,
                       pain_before: int | None = None) -> dict[str, Any]:
        ex = library.get(exercise_id)
        item = item or {}
        mv = ex["movement"]
        self._require_idle()
        if not self.is_calibrated(MOVEMENTS[mv].joint):
            raise RehabError("not_calibrated", f"calibrate the {MOVEMENTS[mv].joint} first")
        if pain_before is not None and pain_before >= self.c.pain_stop:
            raise RehabError("pain_too_high", "pain is too high to exercise today")
        c = self.c
        target = float(item.get("target_angle") or ex["target_pct"] / 100 * c.normal_rom[mv])
        limit = item.get("limit_angle") or self._limits().get(mv)
        if limit is not None:
            target = min(target, float(limit))
        up_s, down_s = item.get("tempo_s", ex["tempo_s"])
        hold = float(item.get("hold_s", ex["hold_s"]))
        sets, reps = int(item.get("sets", ex["sets"])), int(item.get("reps", ex["reps"]))
        counter = RepCounter(target, c.rep_up_frac, c.rep_down_frac, c.in_target_frac,
                             min_rep_s=c.too_fast_factor * nominal_duration(up_s, down_s, c.rep_down_frac), max_speed_dps=c.max_speed_dps,
                             limit_deg=None if limit is None else float(limit))
        self.mode, self.movement = "exercise", mv
        self._ex = {"id": exercise_id, "movement": mv, "joint": MOVEMENTS[mv].joint, "counter": counter,
                    "target": target, "limit": limit, "sets": sets, "reps": reps, "hold_s": hold,
                    "t0": self.now, "pain_before": pain_before, "over_notified": False,
                    "last_fast": False}
        self.band_command(f"RATE,{c.band_rate_exercise_hz}")
        self.last_result = None
        return self.state()

    def stop_exercise(self, reason: str = "user", pain_after: int | None = None) -> dict[str, Any]:
        if self.mode != "exercise":
            raise RehabError("not_running", "no exercise is running")
        e = self._ex
        self.mode, self.movement = "idle", None
        self.band_command(f"RATE,{self.c.band_rate_idle_hz}")
        s = e["counter"].summary()
        res = {"kind": "session", "exercise_id": e["id"], "stopped_reason": reason, **s,
               "target_angle": e["target"], "limit_angle": e["limit"], "planned_reps": e["sets"] * e["reps"],
               "duration_s": round(self.now - e["t0"], 1), "pain_before": e["pain_before"],
               "pain_after": pain_after}
        if self.store is not None:
            plans = self.store.list("exercise_plans", order="version DESC", limit=1)
            self.store.upsert("exercise_sessions", {
                "program_id": self.program_id(), "plan_id": plans[0]["id"] if plans else None,
                "start_ts": self.epoch_ms(e["t0"]), "end_ts": self.epoch_ms(self.now),
                "exercise_id": e["id"], "reps_done": s["reps"], "avg_peak_angle": s["avg_peak_angle"],
                "max_angle": s["max_angle"], "target_hit_pct": s["target_hit_pct"],
                "too_fast_count": s["too_fast_count"], "pain_before": e["pain_before"],
                "pain_after": pain_after, "stopped_reason": reason})
        self.last_result = res
        self.emit({"t": "exercise_done", **res})
        return res

    def tick(self, now: float) -> None:
        """Watchdog: a missing band or grip sensor must never leave the engine stuck in a mode."""
        self.now = max(self.now, now)
        if self.mode == "calibrating" and now - self._started > self.c.calibration_s + 6:
            joint = self._cal["joint"]
            self.mode = "idle"
            self.band_command(f"RATE,{self.c.band_rate_idle_hz}")
            self.last_result = {"kind": "calibration", "joint": joint, "ok": False, "error": "no_band_data"}
        elif self.mode == "grip" and now - self._started > self.c.grip_test_s + 3:
            self._finish_grip()

    # ---- data in ----------------------------------------------------------------------------
    def on_pair(self, t: float, q_ref: tuple, q_mov: tuple) -> None:
        self.now = t
        if self.mode == "calibrating":
            cs = self._cal
            if cs["t0"] is None:
                cs["t0"] = t
            cs["pairs"].append((q_ref, q_mov))
            if t - cs["t0"] >= self.c.calibration_s:
                self._finish_calibration()
            return
        if self.mode not in ("rom", "exercise") or self.movement is None:
            return
        ja = self.angles[MOVEMENTS[self.movement].joint]
        ang, off = ja.angle(q_ref, q_mov, self.movement)
        self.last_angle, self.last_off_axis = ang, off
        if self.mode == "rom":
            r = self._rom
            r["min"] = ang if r["min"] is None else min(r["min"], ang)
            r["max"] = ang if r["max"] is None else max(r["max"], ang)
            self._n += 1
            if self._n % self.c.angle_msg_every == 0:
                self.emit({"t": "angle", "ts": self.epoch_ms(t), "mode": "rom", "movement": self.movement,
                           "angle": round(ang, 1)})
            return
        e = self._ex
        counter: RepCounter = e["counter"]
        rep = counter.update(ang, t)
        if rep is not None:
            self._on_rep(rep)
        if counter.over_limit_now and not e["over_notified"]:
            e["over_notified"] = True
            self.band_command(f"VIB,{self.c.haptic_ms * 2}")
            self.emit({"t": "limit_exceeded", "ts": self.epoch_ms(t), "angle": round(ang, 1),
                       "limit": e["limit"]})
        elif not counter.over_limit_now:
            e["over_notified"] = False
        self._n += 1
        if self._n % self.c.angle_msg_every == 0:
            self.emit({"t": "angle", "ts": self.epoch_ms(t), "mode": "exercise", "joint": e["joint"],
                       "movement": e["movement"], "angle": round(ang, 1), "rep": counter.count,
                       "target": e["target"], "limit": e["limit"], "in_target": counter.in_target,
                       "too_fast": e["last_fast"], "over_limit": counter.over_limit_now})
        if counter.count >= e["sets"] * e["reps"]:
            self.stop_exercise("completed")

    def _on_rep(self, rep: Rep) -> None:
        e = self._ex
        e["last_fast"] = rep.too_fast
        if rep.too_fast:
            self.band_command(f"VIB,{self.c.haptic_ms}")
        self.emit({"t": "rep", "ts": self.epoch_ms(rep.t_end), **rep.__dict__,
                   "set": (rep.n - 1) // e["reps"] + 1, "rep_in_set": (rep.n - 1) % e["reps"] + 1})

    # ---- helpers ----------------------------------------------------------------------------
    def _movement(self, key: str):  # noqa: ANN202
        if key not in MOVEMENTS:
            raise RehabError("unknown_movement", key)
        return MOVEMENTS[key]

    def _require_idle(self) -> None:
        if self.mode != "idle":
            raise RehabError("busy", f"{self.mode} is in progress")

    def _limits(self) -> dict[str, float]:
        if self.store is None:
            return {}
        pid = self.program_id()
        rows = self.store.list("recovery_cases", "program_id=?", (pid,), limit=1) if pid else []
        return dict(rows[0].get("max_angle_limits_json") or {}) if rows else {}

    def state(self) -> dict[str, Any]:
        e = self._ex if self.mode == "exercise" else None
        return {"mode": self.mode, "movement": self.movement, "angle": self.last_angle,
                "calibrated": sorted(self.angles), "result": self.last_result,
                "reps": e["counter"].count if e else None,
                "target": e["target"] if e else None,
                "movements": {j: movements_of(j) for j in {m.joint for m in MOVEMENTS.values()}}}

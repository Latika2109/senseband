"""Recovery cases, plans, progress and forecast (store-backed orchestration for the API)."""
from __future__ import annotations

import time
from datetime import date
from typing import Any

from ..config import Settings
from ..storage.db import Store
from . import forecast as fc
from .joints import JOINTS, MOVEMENTS
from .plan import generate_plan

MAIN_MOVEMENT = {"shoulder": "shoulder_flexion", "elbow": "elbow_flexion", "wrist": "wrist_flexion",
                 "hip": "hip_flexion", "knee": "knee_flexion", "ankle": "ankle_dorsiflexion"}
DAY_MS = 86_400_000


class RecoveryService:
    def __init__(self, cfg: Settings, store: Store):
        self.cfg, self.store = cfg, store

    # ---- recovery case ----------------------------------------------------------------------
    @staticmethod
    def validate_case(case: dict[str, Any]) -> dict[str, float]:
        joint = case.get("joint")
        if joint not in JOINTS:
            raise ValueError(f"joint must be one of {', '.join(JOINTS)}")
        if case.get("side") not in ("left", "right"):
            raise ValueError("side must be 'left' or 'right'")
        limits = {k: float(v) for k, v in (case.get("max_angle_limits") or {}).items()}
        for k, v in limits.items():
            if k not in MOVEMENTS or MOVEMENTS[k].joint != joint or not 0 < v <= 200:
                raise ValueError(f"bad limit for {k}")
        return limits

    def create_case(self, program_id: str, case: dict[str, Any]) -> dict[str, Any]:
        joint = case.get("joint")
        limits = self.validate_case(case)
        return self.store.upsert("recovery_cases", {
            "program_id": program_id, "limb": case.get("limb") or ("arm" if joint in (
                "shoulder", "elbow", "wrist") else "leg"), "joint": joint, "side": case["side"],
            "injury_type": case.get("injury_type", "fracture"), "injury_date": case.get("injury_date"),
            "cast_removed_date": case.get("cast_removed_date"), "max_angle_limits_json": limits,
            "weight_bearing": case.get("weight_bearing"), "doctor_approved": 0})

    def case(self, program_id: str | None) -> dict[str, Any] | None:
        if not program_id:
            return None
        rows = self.store.list("recovery_cases", "program_id=?", (program_id,), limit=1)
        return rows[0] if rows else None

    def set_limits(self, program_id: str, limits: dict[str, float], approved: bool) -> dict[str, Any]:
        c = self.case(program_id)
        if c is None:
            raise KeyError("no recovery case")
        for k, v in limits.items():
            if k not in MOVEMENTS or MOVEMENTS[k].joint != c["joint"] or not 0 < float(v) <= 200:
                raise ValueError(f"bad limit for {k}")
        self.store.update("recovery_cases", c["id"], {
            "max_angle_limits_json": {k: float(v) for k, v in limits.items()},
            "doctor_approved": int(approved)})
        return self.case(program_id) or c

    # ---- assessments ------------------------------------------------------------------------
    def _rows(self, program_id: str, kind: str) -> list[dict[str, Any]]:
        return self.store.list("assessments", "program_id=? AND kind=?", (program_id, kind),
                               order="ts", limit=5000)

    def _days(self, ts: int) -> int:
        return ts // DAY_MS

    # ---- plan -------------------------------------------------------------------------------
    def generate(self, program_id: str, doctor_linked: bool = False) -> dict[str, Any]:
        c = self.case(program_id)
        if c is None:
            raise KeyError("no recovery case")
        mv = MAIN_MOVEMENT[c["joint"]]
        roms = [r for r in self._rows(program_id, "rom") if r["movement"] == mv]
        if not roms:
            raise LookupError("do a ROM assessment first")
        rom = roms[-1]["rom"] or 0.0
        pains = self._rows(program_id, "pain")
        pain = pains[-1]["pain_0_10"] if pains else None
        days = None
        if c.get("injury_date"):
            try:
                days = (date.today() - date.fromisoformat(c["injury_date"])).days
            except ValueError:
                days = None
        plan = generate_plan(self.cfg.rehab, c["joint"], mv, rom, pain, days, self.adherence(program_id),
                             c.get("max_angle_limits_json") or {}, doctor_linked)
        prev = self.store.list("exercise_plans", "program_id=?", (program_id,), order="version DESC",
                               limit=1)
        row = self.store.upsert("exercise_plans", {
            "program_id": program_id, "version": (prev[0]["version"] + 1) if prev else 1,
            "created_by": "system", "approved_by": None, "stage": str(plan["stage"]),
            "items_json": {"items": plan["items"], "notes": plan["notes"], "rom_pct": plan["rom_pct"],
                           "may_start": plan["may_start"], "movement": mv}})
        return self.view_plan(row)

    def view_plan(self, row: dict[str, Any]) -> dict[str, Any]:
        body = row.get("items_json") or {}
        approved = row.get("approved_by")
        status = "approved" if approved else ("may_start_stage1" if body.get("may_start")
                                              else "waiting_for_doctor")
        return {"id": row["id"], "version": row["version"], "stage": int(row["stage"]),
                "created_by": row["created_by"], "approved_by": approved, "status": status,
                "items": body.get("items", []), "notes": body.get("notes", []),
                "rom_pct": body.get("rom_pct"), "movement": body.get("movement")}

    def current_plan(self, program_id: str | None) -> dict[str, Any] | None:
        if not program_id:
            return None
        rows = self.store.list("exercise_plans", "program_id=?", (program_id,), order="version DESC",
                               limit=1)
        return self.view_plan(rows[0]) if rows else None

    def approve(self, program_id: str, by: str) -> dict[str, Any]:
        p = self.current_plan(program_id)
        if p is None:
            raise KeyError("no plan")
        self.store.update("exercise_plans", p["id"], {"approved_by": by})
        return self.current_plan(program_id) or p

    # ---- adherence / progress ---------------------------------------------------------------
    def adherence(self, program_id: str) -> float | None:
        ss = self.store.list("exercise_sessions", "program_id=?", (program_id,), order="start_ts",
                             limit=5000)
        if not ss:
            return None
        first = self._days(ss[0]["start_ts"])
        today = self._days(int(time.time() * 1000))
        planned = max(1.0, (today - first + 1) * self.cfg.rehab.sessions_per_day)
        done = len({self._days(s["start_ts"]) for s in ss})
        return min(1.0, done / planned)

    def progress(self, program_id: str | None) -> dict[str, Any]:
        c = self.case(program_id)
        if c is None or program_id is None:
            return {"case": None}
        mv = MAIN_MOVEMENT[c["joint"]]
        normal = self.cfg.rehab.normal_rom[mv]
        by_day: dict[int, float] = {}
        for r in self._rows(program_id, "rom"):
            if r["movement"] == mv and r["rom"] is not None:
                d = self._days(r["ts"])
                by_day[d] = max(by_day.get(d, 0.0), r["rom"])
        days = sorted(by_day)
        rom_series = [{"day": d * DAY_MS, "rom": by_day[d], "pct": round(100 * by_day[d] / normal, 1)}
                      for d in days]
        grip: dict[str, list[dict[str, Any]]] = {"injured": [], "healthy": []}
        for g in self._rows(program_id, "grip"):
            grip["injured" if g["side"] == c["side"] else "healthy"].append(
                {"ts": g["ts"], "peak": g["grip_peak"], "mean": g["grip_mean"]})
        sessions = self.store.list("exercise_sessions", "program_id=?", (program_id,),
                                   order="start_ts", limit=5000)
        limits = c.get("max_angle_limits_json") or {}
        target = self.cfg.rehab.forecast_target_pct / 100 * normal
        if mv in limits:
            target = min(target, limits[mv])
        f = fc.forecast([float(d - days[0]) for d in days], [by_day[d] for d in days], target, normal,
                        self.cfg.rehab.forecast_min_sessions, self.cfg.rehab.forecast_bootstrap) \
            if days else {"ok": False, "reason": "not_enough_sessions", "n": 0}
        if f.get("ok") and days:
            for k_in, k_out in (("est_days", "est_ts"), ("ci_low_days", "ci_low_ts"),
                                ("ci_high_days", "ci_high_ts")):
                if f.get(k_in) is not None:
                    f[k_out] = int(days[0] * DAY_MS + f[k_in] * DAY_MS)
        return {"case": c, "movement": mv, "normal_rom": normal, "target": round(target, 1),
                "rom": rom_series, "grip": grip, "sessions": sessions,
                "adherence": self.adherence(program_id), "forecast": f}

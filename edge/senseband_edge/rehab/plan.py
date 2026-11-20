"""Plan generator (model M9): rules over the exercise library. Output is a DEFAULT that a doctor or
physio must approve or edit; the doctor's maximum angle always wins."""
from __future__ import annotations

from typing import Any

from ..config import Rehab
from . import library


def rom_percent(rehab: Rehab, movement: str, rom_deg: float) -> float:
    return max(0.0, min(100.0, 100.0 * rom_deg / rehab.normal_rom[movement]))


def stage_for(rehab: Rehab, pct: float) -> int:
    lo, hi = rehab.stage_bounds_pct
    return 1 if pct < lo else 2 if pct < hi else 3


def generate_plan(rehab: Rehab, joint: str, movement: str, rom_deg: float, pain: int | None,
                  days_since_injury: int | None, adherence: float | None,
                  limits: dict[str, float] | None = None, doctor_linked: bool = False,
                  ) -> dict[str, Any]:
    """Stage + exercise list. Never exceeds the doctor's limit; high pain keeps it gentle."""
    limits = limits or {}
    pct = rom_percent(rehab, movement, rom_deg)
    stage = stage_for(rehab, pct)
    notes: list[str] = []
    if pain is not None and pain >= rehab.pain_stop:
        stage = 1
        notes.append("pain_high_rest")                     # very high pain: rest, gentle only
    elif pain is not None and pain >= rehab.pain_gentle_at and stage > 1:
        stage -= 1
        notes.append("pain_kept_gentle")
    if days_since_injury is not None and days_since_injury < 14 and stage > 1:
        stage = 1                                          # first two weeks: gentle mobility only
        notes.append("early_injury_gentle")
    low_adh = adherence is not None and adherence < rehab.low_adherence
    if low_adh:
        notes.append("low_adherence_no_increase")
    items: list[dict[str, Any]] = []
    for ex in library.for_joint(joint, stage):
        mv = ex["movement"]
        normal = rehab.normal_rom[mv]
        target = ex["target_pct"] / 100.0 * normal
        if mv == movement:                                 # progress a little beyond today's range
            target = min(target, max(rom_deg * 1.1, rom_deg + 5)) if stage > 1 else target
        if mv in limits:
            target = min(target, limits[mv])
        reps = ex["reps"] if not low_adh else max(5, ex["reps"] - 3)
        items.append({"exercise_id": ex["id"], "movement": mv, "sets": ex["sets"], "reps": reps,
                      "target_angle": round(target, 1), "hold_s": ex["hold_s"],
                      "tempo_s": ex["tempo_s"], "limit_angle": limits.get(mv)})
    approved = False
    if not doctor_linked and stage == 1:
        notes.append("stage1_may_start_without_doctor")   # clear notice shown in the app
    elif not doctor_linked:
        notes.append("needs_doctor_approval")
    return {"stage": stage, "rom_pct": round(pct, 1), "items": items, "notes": notes,
            "needs_approval": not approved, "may_start": doctor_linked or stage == 1}

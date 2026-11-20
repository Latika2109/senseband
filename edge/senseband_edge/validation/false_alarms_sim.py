"""False-alarm benchmark on SIMULATED benign scenarios (a regression guard, not field evidence).

Every scenario here contains no real emergency, so any alert that is not on its allow-list counts as a false alarm.
Allowed: 'check sensor' notices where the scenario breaks a sensor on purpose, and the designed sleep-desaturation
warning on the sleeping scenario."""
from __future__ import annotations

from typing import Any

ALLOWED: dict[str, set[str]] = {
    "resting": set(), "jogging": set(), "elbow_exercise": set(), "knee_exercise": set(), "grip_test": set(),
    "sleeping": {"sleep_desats"}, "lead_off": {"check_sensor_ecg", "check_sensor_ppg"}, "noisy_ecg": {"check_sensor_ecg"},
    "day_compressed": {"sleep_desats"},
}


def run_benign(scenarios: list[str], seeds: list[int], seconds: float | None, settings: Any = None) -> dict[str, Any]:
    from tests.harness import run_scenario  # imported lazily: the harness lives with the tests
    total_s, bad = 0.0, []
    for name in scenarios:
        for seed in seeds:
            r = run_scenario(name, seconds, settings=settings, seed=seed)
            total_s += r.engine.now
            for a in r.engine.alerts.alerts:
                if a.kind not in ALLOWED[name]:
                    bad.append({"scenario": name, "seed": seed, "kind": a.kind, "level": a.level, "t": round(a.t, 1)})
    hours = total_s / 3600.0
    crit = [b for b in bad if b["level"] == "critical"]
    return {"hours_simulated": round(hours, 2), "false_alarms": bad, "false_critical": len(crit),
            "per_24h": round(len(bad) * 24.0 / hours, 3) if hours else None}

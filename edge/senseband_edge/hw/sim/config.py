"""Load the simulator config and scenario files."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SCENARIO_DIR = HERE / "scenarios"


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Simulator config. Override the file with the SENSEBAND_SIM_CONFIG env var."""
    p = path or os.environ.get("SENSEBAND_SIM_CONFIG") or HERE / "sim_config.yaml"
    return _load(str(p))


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))


def scenario_path(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return p
    q = SCENARIO_DIR / f"{name_or_path}.yaml"
    if not q.exists():
        raise FileNotFoundError(
            f"unknown scenario '{name_or_path}'. Available: {', '.join(list_scenarios())}"
        )
    return q


def load_scenario_dict(name_or_path: str) -> dict[str, Any]:
    with open(scenario_path(name_or_path)) as f:
        return yaml.safe_load(f)

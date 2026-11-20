"""Exercise library (data in config/exercises.yaml) and lookup helpers."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parents[2] / "config" / "exercises.yaml"


@lru_cache(maxsize=1)
def library() -> dict[str, dict[str, Any]]:
    with open(PATH) as f:
        items = yaml.safe_load(f)["exercises"]
    ids = [e["id"] for e in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate exercise ids")
    return {e["id"]: e for e in items}


def get(exercise_id: str) -> dict[str, Any]:
    try:
        return library()[exercise_id]
    except KeyError as e:
        raise KeyError(f"unknown exercise '{exercise_id}'") from e


def for_joint(joint: str, stage: int | None = None) -> list[dict[str, Any]]:
    return [e for e in library().values()
            if e["joint"] == joint and (stage is None or e["stage"] == stage)]

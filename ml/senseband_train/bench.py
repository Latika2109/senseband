"""python -m senseband_train.bench  - inference latency + size for every model in ml/models.

Run it ON THE RASPBERRY PI 3 to check the budget (< 50 ms per window, < 5 MB per model). On a laptop it only
shows the pipeline works; numbers here are not Pi numbers."""
from __future__ import annotations

import json
import platform
import sys

from .core import MAX_LATENCY_MS, MAX_MODEL_BYTES, MODELS_DIR, latency_ms, onnx_session


def main() -> int:
    metrics = json.loads((MODELS_DIR / "metrics.json").read_text())
    print(f"machine: {platform.machine()} / {platform.processor() or platform.platform()}")
    bad = 0
    for name, m in sorted(metrics.items()):
        path = MODELS_DIR / m["onnx"]["file"]
        lat = latency_ms(onnx_session(path), len(m["features"]), runs=500)
        size = path.stat().st_size
        ok = lat["p95"] < MAX_LATENCY_MS and size < MAX_MODEL_BYTES
        bad += not ok
        print(f"{name:18s} {size / 1024:7.0f} KB  median {lat['median']:6.2f} ms  p95 {lat['p95']:6.2f} ms  {'OK' if ok else 'OVER BUDGET'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

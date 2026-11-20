"""python -m senseband_train [m1 m2 m3 m4 m5 | all] [--quick] [--out DIR]   (also: make train)"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import MODELS_DIR
from .report import write_markdown
from .train_models import ALL, run


def main() -> int:
    ap = argparse.ArgumentParser(description="Train SenseBand models and export ONNX")
    ap.add_argument("models", nargs="*", default=["all"], help="m1..m5 or all")
    ap.add_argument("--quick", action="store_true", help="small subsets (smoke test, not for real metrics)")
    ap.add_argument("--out", default=str(MODELS_DIR))
    a = ap.parse_args()
    names = list(ALL) if "all" in a.models else a.models
    bad = [n for n in names if n not in ALL]
    if bad:
        print("unknown model(s):", bad, file=sys.stderr)
        return 2
    run(names, Path(a.out), a.quick)
    write_markdown(Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

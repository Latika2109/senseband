"""python -m senseband_train.prepare [uci_har] [pamap2] [cinc2017] [all]  - download + safely extract datasets."""
from __future__ import annotations

import sys

from .datasets import URLS, fetch


def main(argv: list[str]) -> int:
    names = [a for a in argv if a in URLS] or (list(URLS) if "all" in argv or not argv else [])
    if not names:
        print("usage: prepare [uci_har|pamap2|cinc2017|all]   (SisFall must be downloaded by hand)")
        return 2
    for n in names:
        print(f"{n}: {fetch(n)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

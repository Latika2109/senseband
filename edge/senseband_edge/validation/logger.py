"""Log the band's live values to CSV during a field study (run on a laptop next to the volunteer):

    python -m senseband_edge.validation.logger ws://senseband.local:8080/ws/live study_V01.csv

Each row is the band's reading at that second; you join it with the reference device's readings (taken by hand or
exported) using `pair_by_time`. The volunteer id and the condition go in the file name / a notes column you add."""
from __future__ import annotations

import csv
import sys
import time
from typing import Any

COLUMNS = ["t_s", "hr", "hr_src", "spo2", "temp", "context", "ecg_quality", "ppg_quality", "angle", "movement"]


def rows_from_message(msg: dict[str, Any], t_s: float) -> list[list[Any]]:
    """Turn one /ws/live message into zero or one CSV row. Poor-quality values arrive as None and stay empty."""
    if msg.get("t") == "vitals":
        q = msg.get("quality") or {}
        return [[round(t_s, 2), msg.get("hr"), msg.get("hr_src"), msg.get("spo2"), msg.get("temp"), msg.get("context"),
                 q.get("ecg"), q.get("ppg"), "", ""]]
    if msg.get("t") == "angle":
        return [[round(t_s, 2), "", "", "", "", "", "", "", msg.get("angle"), msg.get("movement")]]
    return []


def main(url: str, out: str, seconds: float = 3600) -> int:  # pragma: no cover - needs a live band
    import json

    from websockets.sync.client import connect
    t0 = time.time()
    with connect(url) as ws, open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        while time.time() - t0 < seconds:
            try:
                msg = json.loads(ws.recv(timeout=20))
            except TimeoutError:
                continue
            for row in rows_from_message(msg, time.time() - t0):
                w.writerow(row)
            f.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 3600))

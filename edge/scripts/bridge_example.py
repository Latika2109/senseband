"""Forward the data your Pi already receives into the SenseBand service (no firmware change needed).

Edit `read_my_data()` so it yields what your current script gets, then run on the Pi:

    SENSEBAND_BAND_TOKEN=<token> python3 edge/scripts/bridge_example.py

Our service (python -m senseband_edge / systemd 'senseband') must be running on the same Pi (port 8080).
Message format (one per line, numbers only):
  V,<hr bpm>,<hrValid 0/1>,<spo2 %>,<spo2Valid 0/1>,<finger 0/1>,<perfusion %>     pulse + SpO2, about once a second
  B,<battery %>                                                                      battery, every ~10 s
  A,<ms>,<ax g>,<ay g>,<az g>,<gx deg/s>,<gy deg/s>,<gz deg/s>                       motion, 50 per second (waist band)
  Q,<ms>,<w>,<x>,<y>,<z>                                                             orientation quaternion (joint tracking)
Units matter: accelerometer in g (1.0 = gravity), gyro in deg/s, SpO2 in percent, heart rate in beats per minute.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

URL = os.environ.get("SENSEBAND_URL", "http://127.0.0.1:8080") + "/api/band/ingest"
TOKEN = os.environ["SENSEBAND_BAND_TOKEN"]          # same value the service uses
DEVICE_ID = os.environ.get("DEVICE_ID", "S1")       # S1 = primary band; another id for a second ESP32


def read_my_data():
    """REPLACE THIS. Yield one dict per reading from whatever your Pi already receives (UDP, serial, MQTT, a file...).
    Keys used below are all optional: hr, spo2, finger, battery, ax, ay, az, gx, gy, gz."""
    # Example placeholder: one fake reading per second so you can test the connection end to end.
    while True:
        yield {"hr": 72, "spo2": 97, "finger": 1, "battery": 90, "ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}
        time.sleep(1)


def to_lines(d: dict, ms: int) -> list[str]:
    out = []
    if "hr" in d or "spo2" in d:
        hr, sp = d.get("hr"), d.get("spo2")
        out.append(f"V,{int(hr) if hr else -999},{int(bool(hr))},{int(sp) if sp else -999},{int(bool(sp))},{int(d.get('finger', 1))},2.0")
    if "battery" in d:
        out.append(f"B,{int(d['battery'])}")
    if all(k in d for k in ("ax", "ay", "az", "gx", "gy", "gz")):
        out.append(f"A,{ms},{d['ax']:.3f},{d['ay']:.3f},{d['az']:.3f},{d['gx']:.1f},{d['gy']:.1f},{d['gz']:.1f}")
    return out


def post(lines: list[str]) -> None:
    req = urllib.request.Request(URL, data=("\n".join(lines) + "\n").encode(), method="POST",
                                 headers={"X-Band-Token": TOKEN, "X-Band-Id": DEVICE_ID, "Content-Type": "text/plain", "Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            print(r.read().decode().strip().splitlines()[0])         # "OK <accepted> <rejected>"
    except urllib.error.HTTPError as e:
        print("service refused:", e.code, e.read().decode()[:120])
    except OSError as e:
        print("service not reachable:", e)


def main() -> None:
    batch: list[str] = []
    last = time.time()
    for d in read_my_data():
        batch += to_lines(d, int(time.time() * 1000) % 2 ** 31)
        if time.time() - last >= 0.5 and batch:
            post(batch)
            batch, last = [], time.time()


if __name__ == "__main__":
    main()

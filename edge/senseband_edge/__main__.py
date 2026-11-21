"""Run the edge service:  python -m senseband_edge [--sim] [--scenario jogging] [--speed 1]"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="SenseBand edge service")
    ap.add_argument("--sim", action="store_true", help="simulated sensors (= SENSEBAND_SIM=1)")
    ap.add_argument("--hw", action="store_true", help="real hardware (Raspberry Pi)")
    ap.add_argument("--scenario", default="resting")
    ap.add_argument("--speed", type=float, default=1.0, help="sim speed: 1 = real time, 0 = max")
    ap.add_argument("--no-loop", action="store_true", help="stop at the end of the scenario")
    ap.add_argument("--config", default=None, help="extra YAML merged over config/default.yaml")
    ap.add_argument("--host", default="0.0.0.0")  # noqa: S104 - the app on a phone must reach it
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--db", default=None, help="SQLite path (default: config db_path)")
    a = ap.parse_args()
    if a.sim:
        os.environ["SENSEBAND_SIM"] = "1"
    from .envfile import load_env_files
    load_env_files()                          # .env / SENSEBAND_ENV_FILE / /etc/senseband/senseband.env (real env vars win)
    from .config import load_settings
    over: dict = {}
    if a.sim:
        over["mode"] = "sim"
    if a.hw:
        over["mode"] = "hw"
    if a.db:
        over["db_path"] = a.db
    cfg = load_settings(over, a.config)
    if a.port:
        cfg.port = a.port                     # the pairing info (/api/pair, QR) must advertise the port actually used
    from .logging_setup import setup_logging
    setup_logging(cfg.logging)
    import uvicorn

    from .api.app import create_app
    from .service import build_service
    try:
        svc = build_service(cfg, a.scenario, a.speed, loop=not a.no_loop)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"SenseBand edge: {'SIMULATED' if cfg.is_sim else 'hardware'} sensors"
          f"{' - scenario ' + a.scenario if cfg.is_sim else ''}; API http://{a.host}:"
          f"{a.port or cfg.port}/api/status  live ws://.../ws/live")
    uvicorn.run(create_app(svc), host=a.host, port=a.port or cfg.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())

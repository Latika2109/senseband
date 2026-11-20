"""Rotating file logs (docs/06 M6: log rotation). The SD card must never fill up with logs."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import LoggingCfg


def setup_logging(cfg: LoggingCfg, console: bool = True) -> Path:
    d = Path(cfg.dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "senseband.log"
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))
    for h in list(root.handlers):                       # idempotent: calling twice does not duplicate lines
        if getattr(h, "_senseband", False):
            root.removeHandler(h)
            h.close()
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=int(cfg.max_mb * 1024 * 1024), backupCount=cfg.backups,
                                              encoding="utf-8")
    handlers: list[logging.Handler] = [fh]
    if console:
        handlers.append(logging.StreamHandler())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in handlers:
        h.setFormatter(fmt)
        h._senseband = True  # type: ignore[attr-defined]
        root.addHandler(h)
    return path

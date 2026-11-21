"""Tiny .env loader (no dependency): secrets live in an ignored file or the service environment, never in git."""
from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            continue
        k, v = m.groups()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        out[k] = v
    return out


def default_paths() -> list[Path]:
    paths = []
    if os.environ.get("SENSEBAND_ENV_FILE"):
        paths.append(Path(os.environ["SENSEBAND_ENV_FILE"]))
    return [*paths, Path(".env"), Path("/etc/senseband/senseband.env")]


def load_env_files(paths: list[Path] | None = None, environ: MutableMapping[str, str] | None = None) -> list[Path]:
    """Load KEY=VALUE files into the environment. A variable that is already set in the real environment wins."""
    env = os.environ if environ is None else environ
    loaded = []
    for p in paths if paths is not None else default_paths():
        try:
            data = parse_env(p.read_text())
        except OSError:
            continue
        for k, v in data.items():
            env.setdefault(k, v)
        loaded.append(p)
    return loaded

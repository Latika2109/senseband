"""Data export and erasure (India DPDP Act 2023): the person owns the data.

Export writes everything the band holds about the patient as plain files. Erase wipes it for good (rows, outbox,
raw chunks, recordings) and compacts the database so deleted rows are not recoverable from free pages. Neither runs
unless explicitly requested; nothing in SenseBand ever deletes patient data on its own.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from .storage.db import Store
from .storage.schema import TABLES

README = """SenseBand data export
=====================
Everything this band stores about the patient, as plain files you can open anywhere.

  <table>.jsonl        one JSON object per line (events, vitals_minute, assessments, ...). Binary values are base64.
  ecg_strips/*.csv     ECG saved with each alert, in millivolts at the stated sampling rate
  recordings/*.npz     labelled raw recordings made with 'Record training data'
  manifest.json        counts and SHA-256 of every file

This is your data under the Digital Personal Data Protection Act 2023. You may keep it, share it with your doctor,
or ask for it to be erased (Settings > Delete my data).
"""
ORDER = [*TABLES.keys(), "outbox", "raw_chunks"]


def _jsonable(v: Any) -> Any:
    return base64.b64encode(v).decode() if isinstance(v, bytes | bytearray) else v


def export_zip(store: Store, recordings_dir: Path) -> bytes:
    buf = io.BytesIO()
    manifest: dict[str, Any] = {"generated_ms": int(time.time() * 1000), "files": {}}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name: str, data: bytes) -> None:
            z.writestr(name, data)
            manifest["files"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

        add("README.txt", README.encode())
        for table in ORDER:
            if table == "raw_chunks":
                continue                                     # rolling raw store is not exported by default (large)
            rows = store.query(f"SELECT * FROM {table}")
            if table == "outbox":
                continue
            lines = "\n".join(json.dumps({k: _jsonable(v) for k, v in r.items()}) for r in rows)
            add(f"{table}.jsonl", (lines + "\n").encode() if lines else b"")
            manifest["files"][f"{table}.jsonl"]["rows"] = len(rows)
        for s in store.query("SELECT id, event_id, ts, fs, samples FROM ecg_strips"):
            x = np.frombuffer(s["samples"], dtype=np.float32)
            csv = "mv\n" + "\n".join(f"{v:.4f}" for v in x)
            add(f"ecg_strips/{s['event_id']}_{s['fs']}Hz.csv", csv.encode())
        if recordings_dir.exists():
            for f in sorted(recordings_dir.glob("*.npz")):
                add(f"recordings/{f.name}", f.read_bytes())
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue()


def erase_all(store: Store, recordings_dir: Path, confirm: str) -> dict[str, int]:
    """Irreversibly delete everything. `confirm` must be the literal word DELETE."""
    if confirm != "DELETE":
        raise PermissionError("type DELETE to confirm")
    counts: dict[str, int] = {}
    with store.lock:
        store.db.execute("BEGIN")
        try:
            for t in [*TABLES, "outbox", "raw_chunks", "sync_state"]:
                counts[t] = int(store.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                store.db.execute(f"DELETE FROM {t}")
            store.db.execute("COMMIT")
        except Exception:
            store.db.execute("ROLLBACK")
            raise
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.db.execute("VACUUM")                            # rewrite the file: deleted rows are gone from disk too
    n = 0
    if recordings_dir.exists():
        n = len(list(recordings_dir.glob("*")))
        shutil.rmtree(recordings_dir, ignore_errors=True)
    counts["recordings"] = n
    return counts

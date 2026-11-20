"""In-memory stand-in for the cloud Edge Functions with failure injection (same semantics as the SQL)."""
from __future__ import annotations

from collections import Counter
from typing import Any

from senseband_edge.sync.transport import AuthError, TransportError

AUTHORITATIVE = {"patients", "recovery_cases", "exercise_plans", "notes"}
PK = {"vitals_minute": ("patient_id", "minute_ts")}


class MockCloud:
    def __init__(self, patient_id: str = "patient01"):
        self.patient_id = patient_id
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.delivered: Counter[tuple[str, str]] = Counter()   # how many times each row was RECEIVED
        self.cloud_edited: dict[tuple[str, str], dict[str, Any]] = {}   # rows humans changed in the cloud
        self.online = True
        self.key_ok = True
        self.drop_response_next = 0                  # apply the batch, then lose the response
        self.crash_after_rows: int | None = None     # apply k rows then fail
        self.reject_ids: set[str] = set()            # row_ids the cloud refuses
        self.calls = 0
        self.batches: list[list[dict[str, Any]]] = []
        self.pull_calls = 0

    def _guard(self) -> None:
        self.calls += 1
        if not self.online:
            raise TransportError("network unreachable")
        if not self.key_ok:
            raise AuthError("HTTP 401")

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert path == "/ingest"
        self._guard()
        rows = body["rows"]
        self.batches.append(rows)
        accepted, rejected = [], []
        for i, r in enumerate(rows):
            if self.crash_after_rows is not None and i >= self.crash_after_rows:
                self.crash_after_rows = None
                raise TransportError("connection reset mid-batch")
            key = (r["table"], r["row_id"])
            self.delivered[key] += 1
            if r["row_id"] in self.reject_ids:
                rejected.append({"id": r["id"], "error": "schema violation"})
                continue
            if key in self.cloud_edited and r["table"] in AUTHORITATIVE:
                accepted.append(r["id"])             # "skipped": cloud wins, still acknowledged
                continue
            self.rows[key] = r["payload"]
            accepted.append(r["id"])
        if self.drop_response_next:
            self.drop_response_next -= 1
            raise TransportError("timeout waiting for response")
        return {"accepted": accepted, "rejected": rejected}

    def get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        assert path == "/pull"
        self._guard()
        self.pull_calls += 1
        since = int(params["since"]) if params["since"].isdigit() else 0
        out: dict[str, Any] = {"patients": [], "exercise_plans": [], "recovery_cases": [], "notes": []}
        hi = since
        for (table, _), row in self.cloud_edited.items():
            if row["_v"] > since:
                out[table].append({k: v for k, v in row.items() if k != "_v"})
                hi = max(hi, row["_v"])
        return {**out, "cursor": str(hi)}

    def human_edit(self, table: str, row: dict[str, Any]) -> None:
        v = max([r["_v"] for r in self.cloud_edited.values()], default=0) + 1
        self.cloud_edited[(table, str(row.get("id")))] = {**row, "_v": v}

    # convenience
    def count(self, table: str) -> int:
        return sum(1 for (t, _) in self.rows if t == table)

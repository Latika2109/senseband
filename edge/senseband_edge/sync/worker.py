"""Cloud sync worker (docs/02 rules 1-3): push the outbox, pull profile/plans/limits/notes.

Design goals: bandwidth-light, resumable after any outage, safe to repeat (the cloud upserts by key).
  * rows are coalesced: only the newest version per (table, row) is sent; older versions are dropped
  * small batches by row count AND bytes, events first so alerts reach caregivers before bulk data
  * a failure never loses data: rows stay in the outbox until the cloud acknowledges them
  * exponential backoff after failures; one rejected row never blocks the others
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Cloud
from ..storage.db import Store
from .transport import AuthError, Transport, TransportError

# which cloud-authoritative fields the band accepts for each table (everything else is ignored)
PULL_TABLES = ("patients", "recovery_cases", "exercise_plans", "notes")
PATIENT_FIELDS = ("name", "dob", "sex", "height_cm", "weight_kg", "conditions", "medications", "resting_hr",
                  "emergency_contacts")


@dataclass
class PushResult:
    sent: int = 0
    superseded: int = 0
    rejected: int = 0
    dead: int = 0
    remaining: int = 0
    batches: int = 0


@dataclass
class SyncState:
    online: bool | None = None
    last_ok_ms: int | None = None
    last_error: str | None = None
    failures: int = 0
    next_try: float = 0.0
    last_pull: float = -1e18
    auth_failed: bool = False
    pulled: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class SyncWorker:
    def __init__(self, store: Store, transport: Transport, cfg: Cloud,
                 on_patient: Callable[[dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.t, self.cfg, self.clock = store, transport, cfg, clock
        self.on_patient = on_patient
        self.state = SyncState()
        self._prio = {t: i for i, t in enumerate(cfg.priority)}

    # ---- push -------------------------------------------------------------------------------
    def _next_batch(self) -> tuple[list[dict[str, Any]], dict[tuple[str, str], list[int]]]:
        rows = self.store.pending(self.cfg.batch_rows * 5)
        newest: dict[tuple[str, str], dict[str, Any]] = {}
        older: dict[tuple[str, str], list[int]] = {}
        for r in rows:                                      # ascending id: later rows win
            k = (r["table"], r["row_id"])
            if k in newest:
                older.setdefault(k, []).append(newest[k]["id"])
            newest[k] = r
        ordered = sorted(newest.values(), key=lambda r: (self._prio.get(r["table"], 99), r["id"]))
        batch: list[dict[str, Any]] = []
        size = 0
        for r in ordered:
            n = len(r["payload"])
            if batch and (len(batch) >= self.cfg.batch_rows or size + n > self.cfg.batch_bytes):
                break
            batch.append(r)
            size += n
        return batch, older

    def push_once(self) -> PushResult:
        """Send one batch. Raises TransportError/AuthError; the caller applies backoff."""
        res = PushResult()
        batch, older = self._next_batch()
        if not batch:
            res.remaining = self.store.backlog()
            return res
        body = {"rows": [{"id": r["id"], "table": r["table"], "row_id": r["row_id"], "op": r["op"],
                          "payload": json.loads(r["payload"])} for r in batch]}
        reply = self.t.post("/ingest", body)
        accepted = [int(i) for i in reply.get("accepted", [])]
        ok_ids = set(accepted)
        sup: list[int] = []
        for r in batch:
            if r["id"] in ok_ids:
                sup += older.get((r["table"], r["row_id"]), [])
        self.store.mark_sent(accepted + sup)
        res.sent, res.superseded, res.batches = len(accepted), len(sup), 1
        rej = {int(x["id"]): str(x.get("error", "rejected")) for x in reply.get("rejected", [])}
        if rej:
            counts = self.store.bump_attempts(list(rej), "; ".join(sorted(set(rej.values())))[:280])
            dead = [i for i, n in counts.items() if n >= self.cfg.dead_after_attempts]
            self.store.mark_dead(dead, rej[dead[0]] if dead else "")
            res.rejected, res.dead = len(rej), len(dead)
        res.remaining = self.store.backlog()
        return res

    def push_all(self, max_batches: int = 1000) -> PushResult:
        total = PushResult()
        for _ in range(max_batches):
            r = self.push_once()
            total.sent += r.sent
            total.superseded += r.superseded
            total.rejected += r.rejected
            total.dead += r.dead
            total.batches += r.batches
            total.remaining = r.remaining
            if r.remaining == 0 or r.sent + r.superseded == 0:
                break
        return total

    # ---- pull -------------------------------------------------------------------------------
    def pull_once(self) -> int:
        cursor = self.store.state_get("pull_cursor", "epoch") or "epoch"
        data = self.t.get("/pull", {"since": cursor})
        n = self.apply_pull(data)
        if data.get("cursor"):
            self.store.state_set("pull_cursor", str(data["cursor"]))
        self.state.pulled += n
        return n

    def apply_pull(self, data: dict[str, Any]) -> int:
        """Apply cloud-authoritative rows locally WITHOUT echoing them back to the outbox."""
        n = 0
        for table in PULL_TABLES:
            known = self.store.columns(table)
            for row in data.get(table, []) or []:
                clean = {k: v for k, v in row.items() if k in known}
                if table == "patients":
                    local = self.store.get("patients", clean["id"]) or {}
                    fields = {k: clean[k] for k in PATIENT_FIELDS if k in clean}
                    merged = {**local, "id": clean["id"], **fields}
                    self.store.upsert("patients", merged, outbox=False)
                    if self.on_patient:
                        self.on_patient(self.store.get("patients", clean["id"]) or merged)
                else:
                    self.store.upsert(table, clean, outbox=False)
                n += 1
        return n

    # ---- one scheduling step (called every second by run()) ---------------------------------
    def step(self, force: bool = False) -> dict[str, Any]:
        now, st = self.clock(), self.state
        out: dict[str, Any] = {"pushed": 0, "pulled": 0, "skipped": None}
        if st.auth_failed and not force:
            out["skipped"] = "auth_failed"
            return out
        if now < st.next_try and not force:
            out["skipped"] = "backoff"
            return out
        try:
            if self.store.backlog() or force:
                r = self.push_all()
                out["pushed"] = r.sent
                out["push"] = r
            if force or now - st.last_pull >= self.cfg.pull_every_s:
                out["pulled"] = self.pull_once()
                st.last_pull = now
            self._ok()
        except AuthError as e:
            st.auth_failed, st.online, st.last_error = True, True, f"auth: {e}"
            out["error"] = st.last_error
        except (TransportError, ValueError) as e:
            self._failed(str(e))
            out["error"] = str(e)
        return out

    def _ok(self) -> None:
        st = self.state
        st.online, st.failures, st.last_error, st.auth_failed = True, 0, None, False
        st.next_try = 0.0
        st.last_ok_ms = int(time.time() * 1000)

    def _failed(self, msg: str) -> None:
        st = self.state
        st.online, st.last_error = False, msg
        delay = self.cfg.backoff_s[min(st.failures, len(self.cfg.backoff_s) - 1)]
        st.failures += 1
        st.next_try = self.clock() + delay

    def status(self) -> dict[str, Any]:
        st = self.state
        return {"enabled": True, "online": st.online, "last_ok_ts": st.last_ok_ms,
                "last_error": st.last_error, "backlog": self.store.backlog(), "dead": self.store.dead_count(),
                "retry_in_s": max(0.0, round(st.next_try - self.clock(), 1)),
                "auth_failed": st.auth_failed,
                "pull_cursor": self.store.state_get("pull_cursor")}

    async def run(self) -> None:
        while True:
            await asyncio.to_thread(self.step)
            await asyncio.sleep(1.0)

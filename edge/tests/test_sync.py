"""Milestone 4 (edge side): outbox sync - resumable, idempotent, bandwidth-light, cloud-authoritative pull."""
from __future__ import annotations

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from senseband_edge.alerts.sms import MockSms
from senseband_edge.api.app import create_app
from senseband_edge.config import load_settings
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.source import SimSource
from senseband_edge.service import EdgeService
from senseband_edge.storage.db import Store
from senseband_edge.storage.schema import MIGRATIONS, TABLES
from senseband_edge.sync.transport import AuthError, HttpTransport, TransportError
from senseband_edge.sync.worker import SyncWorker

from .harness import make_settings, run_scenario
from .mockcloud import MockCloud

CFG = make_settings().cloud


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def worker(store=None, cloud=None, cfg=None, on_patient=None):
    store, cloud, clock = store or Store(), cloud or MockCloud(), Clock()
    return SyncWorker(store, cloud, cfg or CFG, on_patient, clock), store, cloud, clock


def vm(store, minute, hr=70.0):
    store.upsert("vitals_minute", {"patient_id": "patient01", "minute_ts": minute * 60000, "hr_ecg": hr})


# ---- push semantics -------------------------------------------------------------------------------
def test_coalesces_versions_and_sends_only_the_newest():
    w, s, c, _ = worker()
    for hr in (60, 61, 62, 63, 64):
        vm(s, 1, hr)                                          # same minute rewritten 5 times
    assert s.backlog() == 5
    r = w.push_once()
    assert r.sent == 1 and r.superseded == 4 and s.backlog() == 0
    assert c.delivered[("vitals_minute", "patient01:60000")] == 1
    assert c.rows[("vitals_minute", "patient01:60000")]["hr_ecg"] == 64      # newest version


def test_events_go_first_and_batches_respect_row_and_byte_limits():
    cfg = CFG.model_copy(update={"batch_rows": 4})
    w, s, c, _ = worker(cfg=cfg)
    for m in range(10):
        vm(s, m)
    s.upsert("events", {"id": "ev1", "patient_id": "patient01", "ts": 1, "level": "critical", "kind": "fall"})
    w.push_all()
    first = c.batches[0]
    assert first[0]["table"] == "events"                      # the alert is not stuck behind bulk data
    assert [len(b) for b in c.batches] == [4, 4, 3]
    assert s.backlog() == 0 and c.count("vitals_minute") == 10
    cfg2 = CFG.model_copy(update={"batch_bytes": 600, "batch_rows": 100})
    w2, s2, c2, _ = worker(cfg=cfg2)
    for m in range(30):
        vm(s2, m)
    w2.push_all()
    assert len(c2.batches) > 3 and all(len(b) >= 1 for b in c2.batches) and c2.count("vitals_minute") == 30


def test_ecg_strip_blob_travels_as_base64_with_the_event():
    w, s, c, _ = worker()
    s.upsert("ecg_strips", {"event_id": "ev1", "ts": 1, "fs": 250, "samples": b"\x01\x02\x03\x04"})
    w.push_all()
    p = next(iter(c.rows.values()))
    assert base64.b64decode(p["samples_b64"]) == b"\x01\x02\x03\x04" and "samples" not in p


def test_two_hour_outage_then_reconnect_delivers_everything_exactly_once_with_backoff():
    """Milestone 4 acceptance: unplug the internet for 2 h, reconnect, everything syncs with no duplicates."""
    w, s, c, clock = worker()
    c.online = False
    attempts_log = []
    n_events = 0
    for sec in range(0, 2 * 3600, 10):                        # 2 hours in 10-second ticks
        clock.t = float(sec)
        if sec % 60 == 0:
            vm(s, sec // 60)                                  # one aggregate per minute
        if sec in (1000, 3000, 5000):
            n_events += 1
            s.upsert("events", {"id": f"ev{sec}", "patient_id": "patient01", "ts": sec, "level": "warning", "kind": "k"})
        before = c.calls
        w.step()
        if c.calls != before:
            attempts_log.append(sec)
    assert s.backlog() == 120 + n_events                      # nothing lost while offline
    assert len(attempts_log) <= 14                            # backoff: not hammering a dead link every 10 s
    gaps = [b - a for a, b in zip(attempts_log, attempts_log[1:], strict=False)]
    assert gaps == sorted(gaps) and gaps[-1] >= 800           # increasing, capped at the last backoff step
    assert w.status()["online"] is False and w.status()["last_error"]
    c.online = True
    clock.t = 7200.0 + 1000.0
    out = w.step()
    assert out["pushed"] == 120 + n_events and s.backlog() == 0
    assert c.count("vitals_minute") == 120 and c.count("events") == n_events
    assert max(c.delivered.values()) == 1                     # every row delivered exactly once
    assert w.status()["online"] is True and w.status()["retry_in_s"] == 0.0


def test_lost_response_resend_is_idempotent_no_duplicates():
    w, s, c, clock = worker()
    for m in range(5):
        vm(s, m)
    c.drop_response_next = 1                                  # cloud applied the batch but we never heard back
    assert "error" in w.step()
    assert s.backlog() == 5 and c.count("vitals_minute") == 5      # edge still holds them; cloud already has them
    clock.t += 100
    assert w.step()["pushed"] == 5
    assert s.backlog() == 0 and c.count("vitals_minute") == 5      # still 5 rows: upsert by key


def test_crash_mid_batch_resumes_and_converges():
    w, s, c, clock = worker()
    for m in range(8):
        vm(s, m)
    c.crash_after_rows = 3
    w.step()
    assert s.backlog() == 8 and c.count("vitals_minute") == 3      # partial apply on the cloud side
    clock.t += 100
    w.step()
    assert s.backlog() == 0 and c.count("vitals_minute") == 8
    assert sum(1 for k, v in c.delivered.items() if v > 1) == 3    # only the 3 re-sent rows saw a repeat


def test_rejected_rows_do_not_block_others_and_are_parked_after_n_attempts():
    cfg = CFG.model_copy(update={"dead_after_attempts": 3})
    w, s, c, clock = worker(cfg=cfg)
    for m in range(3):
        vm(s, m)
    s.upsert("events", {"id": "bad", "patient_id": "patient01", "ts": 1, "level": "info", "kind": "k"})
    c.reject_ids = {"bad"}
    r = w.push_once()
    assert r.sent == 3 and r.rejected == 1 and r.dead == 0 and c.count("vitals_minute") == 3
    for _ in range(2):
        r = w.push_once()
    assert r.dead == 1 and s.backlog() == 0 and s.dead_count() == 1
    calls = c.calls
    assert w.push_once().sent == 0 and c.calls == calls            # parked rows are never re-sent
    assert w.status()["dead"] == 1
    assert "dead:" in s.one("select last_error from outbox where row_id='bad' order by id desc limit 1")["last_error"]


def test_auth_failure_stops_retrying_until_forced_and_never_loses_data():
    w, s, c, clock = worker()
    vm(s, 1)
    c.key_ok = False
    assert w.step()["error"].startswith("auth")
    calls = c.calls
    clock.t += 10_000
    assert w.step()["skipped"] == "auth_failed" and c.calls == calls
    assert w.status()["auth_failed"] is True and s.backlog() == 1
    c.key_ok = True
    assert w.step(force=True)["pushed"] == 1 and w.status()["auth_failed"] is False


# ---- pull: the cloud is the source of truth for profile, plans, limits, notes -----------------------
def test_pull_applies_cloud_edits_without_echoing_them_back():
    seen = []
    w, s, c, clock = worker(on_patient=seen.append)
    s.upsert("patients", {"id": "patient01", "name": "Asha (band)", "conditions": [], "medications": [],
                          "emergency_contacts": []})
    w.push_all()
    base_backlog = s.backlog()
    c.human_edit("patients", {"id": "patient01", "name": "Asha Rao", "medications": ["Metoprolol"], "owner": "x",
                              "emergency_contacts": [{"name": "Son", "phone": "+919999", "relation": "son"}],
                              "updated_at": "2026-11-20", "unknown_col": 1})
    c.human_edit("recovery_cases", {"id": "c1", "patient_id": "patient01", "program_id": "pr1", "joint": "elbow",
                                    "side": "right", "max_angle_limits_json": {"elbow_flexion": 100}, "doctor_approved": 1})
    c.human_edit("exercise_plans", {"id": "pl9", "patient_id": "patient01", "program_id": "pr1", "version": 5,
                                    "created_by": "doctor", "approved_by": "Dr. Rao", "stage": "2",
                                    "items_json": {"items": [{"exercise_id": "elbow_active_flexion"}]}})
    c.human_edit("notes", {"id": "n1", "patient_id": "patient01", "author_name": "Dr. Rao", "ts": 5, "text": "Walk daily"})
    clock.t = 500
    out = w.step()
    assert out["pulled"] == 4
    p = s.get("patients", "patient01")
    assert p["name"] == "Asha Rao" and p["medications"] == ["Metoprolol"] and p["emergency_contacts"][0]["phone"] == "+919999"
    assert seen and seen[-1]["name"] == "Asha Rao"                   # engine hook (contacts / beta blocker)
    assert s.get("recovery_cases", "c1")["max_angle_limits_json"] == {"elbow_flexion": 100}
    assert s.get("exercise_plans", "pl9")["approved_by"] == "Dr. Rao" and s.get("notes", "n1")["author_name"] == "Dr. Rao"
    assert s.backlog() == base_backlog                               # nothing echoed back to the cloud
    assert s.state_get("pull_cursor") == "4"
    clock.t += 500
    assert w.step()["pulled"] == 0                                   # cursor persisted: nothing re-applied
    c.human_edit("notes", {"id": "n2", "patient_id": "patient01", "author_name": "Dr. Rao", "ts": 6, "text": "Rest"})
    clock.t += 500
    assert w.step()["pulled"] == 1


def test_cloud_edit_wins_over_a_later_band_change():
    w, s, c, clock = worker()
    s.upsert("patients", {"id": "patient01", "name": "Band name"})
    c.human_edit("patients", {"id": "patient01", "name": "Doctor-corrected"})
    w.step()
    assert s.get("patients", "patient01")["name"] == "Doctor-corrected"
    s.upsert("patients", {"id": "patient01", "name": "Band tries again"})
    clock.t += 1000
    w.step(force=True)
    assert c.rows.get(("patients", "patient01")) is None             # cloud refused to overwrite its edit
    clock.t += 1000
    c.human_edit("patients", {"id": "patient01", "name": "Doctor-corrected"})   # next pull restores the cloud value
    w.step(force=True)
    assert s.get("patients", "patient01")["name"] == "Doctor-corrected"


def test_pulled_limits_are_enforced_by_the_rehab_engine():
    cfgm = make_settings()
    run = run_scenario("elbow_exercise", 3)
    eng = run.engine
    eng.program_id = "pr1"
    w = SyncWorker(run.store, MockCloud(), cfgm.cloud, clock=Clock())
    w.apply_pull({"recovery_cases": [{"id": "c1", "patient_id": "patient01", "program_id": "pr1", "joint": "elbow",
                                      "side": "right", "max_angle_limits_json": {"elbow_flexion": 85}, "doctor_approved": 1}]})
    assert eng.rehab._limits() == {"elbow_flexion": 85}                # noqa: SLF001


# ---- real HTTP transport ----------------------------------------------------------------------------
class _H(BaseHTTPRequestHandler):
    seen: list[dict] = []
    mode = "ok"

    def log_message(self, *a):  # noqa: ANN002
        pass

    def _reply(self):  # noqa: ANN202
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        _H.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body,
                        "ct": self.headers.get("Content-Type")})
        code = {"ok": 200, "401": 401, "500": 500, "400": 400}[_H.mode]
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps({"accepted": [1], "rejected": []}).encode() if code == 200 else b"{}")

    do_POST = do_GET = _reply  # noqa: N815


@pytest.fixture
def http_server():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _H.seen, _H.mode = [], "ok"
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_http_transport_headers_json_and_error_mapping(http_server):
    t = HttpTransport(http_server + "/functions/v1/", "k" * 20, timeout_s=3)
    assert t.post("/ingest", {"rows": []}) == {"accepted": [1], "rejected": []}
    assert _H.seen[0]["auth"] == "Bearer " + "k" * 20 and _H.seen[0]["ct"] == "application/json"
    assert _H.seen[0]["path"] == "/functions/v1/ingest" and json.loads(_H.seen[0]["body"]) == {"rows": []}
    t.get("/pull", {"since": "2026-11-20 10:00:00+00"})
    assert _H.seen[1]["path"].startswith("/functions/v1/pull?since=2026-11-20")
    for mode, exc in (("401", AuthError), ("500", TransportError), ("400", ValueError)):
        _H.mode = mode
        with pytest.raises(exc):
            t.post("/ingest", {"rows": []})
    with pytest.raises(TransportError):
        HttpTransport("http://127.0.0.1:1", "k" * 20, timeout_s=1).post("/ingest", {})   # connection refused


# ---- service integration -------------------------------------------------------------------------------
def svc_cloud(enabled: bool):
    cfg = load_settings({"mode": "sim", "patient": {"id": "patient01"}, "sms": {"retry_delay_s": 0},
                         "cloud": {"enabled": enabled, "url": "https://x.example/functions/v1", "device_key": "k" * 24}})
    return EdgeService(cfg, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(":memory:"))


def test_api_sync_now_and_status_with_and_without_cloud():
    off = svc_cloud(False)
    with TestClient(create_app(off, autostart=False)) as c:
        assert c.post("/api/sync/now").json() == {"enabled": False}
        assert c.get("/api/status").json()["sync"]["enabled"] is False
    on = svc_cloud(True)
    cloud = MockCloud()
    on.sync.t = cloud                                                    # replace the network with the mock
    on.store.upsert("notes", {"patient_id": "patient01", "ts": 1, "text": "x"})
    with TestClient(create_app(on, autostart=False)) as c:
        r = c.post("/api/sync/now").json()
        assert r["enabled"] and r["online"] is True and r["backlog"] == 0 and r["result"]["pushed"] >= 1
        assert c.get("/api/status").json()["sync"]["last_ok_ts"] is not None
        cloud.online = False
        on.store.upsert("notes", {"patient_id": "patient01", "ts": 2, "text": "y"})
        r2 = c.post("/api/sync/now").json()
        assert r2["online"] is False and r2["backlog"] == 1 and "unreachable" in r2["last_error"]
        cloud.human_edit("notes", {"id": "n9", "patient_id": "patient01", "author_name": "Dr. Rao", "ts": 9, "text": "hi"})
        cloud.online = True
        c.post("/api/sync/now")
        assert any(n["author_name"] == "Dr. Rao" for n in c.get("/api/notes").json())


# ---- schema agreement -----------------------------------------------------------------------------------
def cloud_columns() -> dict[str, set[str]]:
    sql = (Path(__file__).resolve().parents[2] / "cloud/supabase/migrations/0001_schema.sql").read_text()
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"create table public\.(\w+) \((.*?)\n\);", sql, re.S):
        body, depth, piece, pieces = m.group(2), 0, "", []
        for ch in body:                                   # split on commas that are not inside (...)
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                pieces.append(piece)
                piece = ""
            else:
                piece += ch
        pieces.append(piece)
        cols = set()
        for p in pieces:
            tok = p.strip().split()
            if tok and tok[0] not in ("primary", "check", "unique", "constraint", "foreign") and re.fullmatch(r"[a-z_0-9]+", tok[0]):
                cols.add(tok[0])
        out[m.group(1)] = cols
    return out


def test_every_field_the_band_sends_exists_in_the_cloud_schema():
    """Catches schema drift between edge/storage/schema.py and cloud/supabase/migrations."""
    cols = cloud_columns()
    r = run_scenario("fall")                                           # events, strips, vitals, programs...
    s = r.store
    s.upsert("assessments", {"program_id": "p", "ts": 1, "kind": "rom", "rom": 90.0, "phase": "baseline"})
    s.upsert("exercise_sessions", {"program_id": "p", "start_ts": 1, "reps_done": 3})
    s.upsert("calibrations", {"program_id": "p", "ts": 1, "joint": "elbow", "offsets_json": {"q": [1, 0, 0, 0]}})
    s.upsert("recovery_cases", {"program_id": "p", "joint": "elbow", "side": "right", "doctor_approved": 0})
    s.upsert("exercise_plans", {"program_id": "p", "version": 1, "created_by": "system", "stage": "1", "items_json": {}})
    s.upsert("forecasts", {"program_id": "p", "ts": 1, "params_json": {}})
    s.upsert("notes", {"patient_id": "patient01", "ts": 1, "text": "x"})
    s.upsert("patients", {"id": "patient01", "name": "A"})
    s.upsert("programs", {"patient_id": "patient01", "type": "monitoring", "purpose": "general", "start_ts": 1, "status": "active"})
    seen = set()
    for p in s.pending(5000):
        t, payload = p["table"], json.loads(p["payload"])
        seen.add(t)
        extras = set(payload) - cols[t] - {"samples_b64"}
        assert not extras, f"{t}: edge sends fields the cloud table lacks: {extras}"
    assert {"events", "ecg_strips", "vitals_minute", "assessments", "exercise_sessions", "calibrations", "recovery_cases",
            "exercise_plans", "forecasts", "notes", "patients", "programs"} <= seen
    assert set(TABLES) - {"devices"} <= set(cols)                      # every synced edge table exists in the cloud


def test_migration_2_upgrades_an_existing_v1_database_without_losing_data(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    for stmt in MIGRATIONS[0].split(";"):
        if stmt.strip():
            raw.execute(stmt)
    raw.execute("PRAGMA user_version=1")
    raw.execute("INSERT INTO notes(id, patient_id, ts, text) VALUES('n','p',1,'keep me')")
    raw.execute('INSERT INTO outbox("table", row_id, op, payload, created_ts) VALUES(\'notes\',\'n\',\'upsert\',\'{}\',1)')
    raw.commit()
    raw.close()
    s = Store(path)
    assert s.version == len(MIGRATIONS) >= 2
    assert s.get("notes", "n")["text"] == "keep me" and s.backlog() == 1
    assert {"attempts", "last_error"} <= s.columns("outbox") and "author_name" in s.columns("notes")
    assert s.state_get("x", "d") == "d"
    s.state_set("x", "1")
    s.state_set("x", "2")
    assert s.state_get("x") == "2"


def test_band_gets_a_unique_persistent_patient_id_and_can_be_linked_from_the_app(tmp_path):
    dbp = str(tmp_path / "b.db")
    cfg = load_settings({"mode": "sim"})                                  # patient.id defaults to "auto"
    assert cfg.patient.id == "auto"
    a = EdgeService(cfg, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(dbp))
    pid = a.cfg.patient.id
    assert re.fullmatch(r"p-[0-9a-f]{12}", pid) and a.store.get("patients", pid)
    cfg2 = load_settings({"mode": "sim"})
    b = EdgeService(cfg2, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(dbp))
    assert b.cfg.patient.id == pid                                         # same band, same id after a restart
    other = load_settings({"mode": "sim"})
    c = EdgeService(other, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(":memory:"))
    assert c.cfg.patient.id != pid                                         # different band, different id
    with TestClient(create_app(a, autostart=False)) as cl:
        assert cl.get("/api/status").json()["sync"]["enabled"] is False
        assert cl.post("/api/cloud/link", json={"url": "nope", "device_key": "x"}).status_code == 422
        r = cl.post("/api/cloud/link", json={"url": "https://x.supabase.co/functions/v1/", "device_key": "k" * 32})
        assert r.status_code == 200 and r.json()["patient_id"] == pid and r.json()["enabled"] is True
        assert a.store.state_get("cloud_url") == "https://x.supabase.co/functions/v1"
        assert cl.get("/api/status").json()["sync"]["enabled"] is True
    d = EdgeService(load_settings({"mode": "sim"}), SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(dbp))
    assert d.sync is not None                                              # the link survives a restart
    d.unlink_cloud()
    assert d.sync is None
    e = EdgeService(load_settings({"mode": "sim"}), SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(dbp))
    assert e.sync is None

"""REST + WebSocket API on the simulator (FastAPI TestClient, real service loop)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from senseband_edge.alerts.sms import MockSms
from senseband_edge.api.app import create_app
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.source import SimSource
from senseband_edge.service import EdgeService
from senseband_edge.storage.db import Store

from .harness import make_settings


def make(scenario="resting", speed=0.0, loop=False):
    cfg = make_settings()
    sms, io = MockSms(), SimIO()
    svc = EdgeService(cfg, SimSource(scenario, speed=speed, loop=loop), sms, io, Store(":memory:"))
    return svc, sms, io


def wait_for(cond, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_status_signal_check_patient_and_programs():
    svc, _, _ = make("resting", speed=20.0, loop=True)
    with TestClient(create_app(svc)) as c:
        assert wait_for(lambda: (svc.engine.vitals.get("quality") or {}).get("ecg") == "good")
        s = c.get("/api/status").json()
        assert s["mode"] == "sim" and s["sensors"]["ecg"] == "good" and s["sensors"]["imu"]
        assert s["band"]["connected"] and s["band"]["battery_pct"] > 90
        assert s["sms"]["modem"] == "mock" and s["program"]["type"] == "monitoring"
        assert s["sync_backlog"] >= 1 and s["errors"] == 0
        sc = c.get("/api/signal-check").json()
        assert sc["ecg"]["quality"] == "good" and sc["ppg"]["quality"] == "good" and sc["ready"]
        # patient profile
        assert c.get("/api/patient").json()["id"] == "patient01"
        r = c.put("/api/patient", json={"name": "Asha", "resting_hr": 64,
                                        "emergency_contacts": [{"name": "Son", "phone": "+919999",
                                                                "relation": "son"}]})
        assert r.status_code == 200 and r.json()["name"] == "Asha"
        assert svc.engine.contacts == ["+919999"] and svc.engine.baseline_hr == 64
        assert c.put("/api/patient", json={"resting_hr": 500}).status_code == 422
        # programs
        p1 = c.get("/api/status").json()["program"]
        p2 = c.post("/api/programs", json={"type": "recovery", "purpose": "elbow fracture", "duration_h": 2,
                                           "recovery_case": {"joint": "elbow", "side": "right"}}).json()
        assert p2["status"] == "active" and p2["planned_end_ts"] - p2["start_ts"] == 7200_000
        assert svc.store.get("programs", p1["id"])["status"] == "stopped"
        assert c.post("/api/programs", json={"type": "bogus"}).status_code == 422
        assert c.post(f"/api/programs/{p2['id']}/stop").json()["status"] == "stopped"
        assert c.post("/api/programs/nope/stop").status_code == 404


def test_websocket_streams_vitals_ecg_and_status():
    svc, _, _ = make("resting", speed=20.0, loop=True)
    with TestClient(create_app(svc)) as c, c.websocket_connect("/ws/live") as ws:
        seen: dict[str, dict] = {}
        for _ in range(400):
            m = ws.receive_json()
            seen.setdefault(m["t"], m)
            if {"status", "vitals", "ecg"} <= set(seen):
                break
        assert {"status", "vitals", "ecg"} <= set(seen)
        v = seen["vitals"]
        for k in ("ts", "hr", "hr_src", "hr_expected", "context", "spo2", "temp", "rhythm",
                  "quality", "ews"):
            assert k in v
        e = seen["ecg"]
        assert e["fs"] == 250 and len(e["samples"]) == 50


def test_fall_flow_over_api_cancel_with_imok():
    svc, sms, io = make("fall", speed=8.0)
    with TestClient(create_app(svc)) as c:
        assert wait_for(lambda: svc.engine.alerts.pending_countdown, 30)
        st = c.get("/api/status").json()
        assert st["alerts"]["countdown"][0]["kind"] == "fall" and st["buzzer"] is not None
        r = c.post("/api/imok", json={"by": "caregiver"}).json()
        assert len(r["cancelled"]) == 1 and r["cancelled"][0]["status"] == "cancelled"
        wait_for(lambda: getattr(svc.source, "finished", False), 30)
        assert sms.log == []
        ev = c.get("/api/history", params={"kind": "events"}).json()
        assert ev["count"] == 1 and ev["rows"][0]["status"] == "cancelled"
        eid = ev["rows"][0]["id"]
        a = c.post(f"/api/events/{eid}/ack", json={"by": "doctor"}).json()
        assert a["acknowledged_by"] == "doctor"
        assert c.post("/api/events/zzz/ack", json={"by": "x"}).status_code == 404


def test_fall_flow_over_api_sms_sent_when_not_cancelled():
    svc, sms, io = make("fall", speed=0.0)
    with TestClient(create_app(svc)):
        assert wait_for(lambda: getattr(svc.source, "finished", False), 30)
        assert wait_for(lambda: len(sms.delivered) == 1, 10)
        assert wait_for(lambda: svc.store.list("events")[0]["sms_sent"] == 1, 10)


def test_button_short_cancels_and_long_press_sos():
    svc, sms, io = make("fall", speed=8.0)
    with TestClient(create_app(svc)):
        assert wait_for(lambda: svc.engine.alerts.pending_countdown, 30)
        io.press_short()                                          # physical "I'm OK"
        assert not svc.engine.alerts.pending_countdown
        io.press_long()                                           # physical SOS
        assert wait_for(lambda: len(sms.delivered) == 1, 10)
        assert "SOS" in sms.delivered[0].text


def test_history_filters_and_validation():
    svc, _, _ = make("resting", speed=0.0)
    with TestClient(create_app(svc)) as c:
        assert wait_for(lambda: getattr(svc.source, "finished", False), 30)
        h = c.get("/api/history", params={"kind": "vitals"}).json()
        assert h["count"] >= 4 and h["rows"][0]["activity"] in ("sitting", "standing")
        t0 = h["rows"][1]["minute_ts"]
        h2 = c.get("/api/history", params={"kind": "vitals", "from": t0}).json()
        assert h2["count"] == h["count"] - 1
        assert c.get("/api/history", params={"kind": "nope"}).status_code == 422


@pytest.mark.slow
def test_memory_stays_under_400mb_on_10_minute_run():
    """Proxy for the Pi 3 budget: the whole service incl. simulator, DB and WebSocket hub."""
    svc, _, _ = make("jogging", speed=0.0)
    with TestClient(create_app(svc)) as c:
        assert wait_for(lambda: getattr(svc.source, "finished", False), 120)
        assert svc.engine.stats["errors"] == 0
        assert c.get("/api/status").json()["rss_mb"] < 400


def test_pair_endpoint_and_cors_for_the_app():
    svc, _, _ = make("resting", speed=0.0)
    with TestClient(create_app(svc, autostart=False)) as c:
        p = c.get("/api/pair").json()
        assert p["name"] == "SenseBand" and p["api"] == 1 and p["port"] == 8080
        assert p["qr"].startswith("senseband://") and p["qr"].endswith(":8080") and p["urls"]
        r = c.options("/api/status", headers={"Origin": "http://localhost:5173",
                                              "Access-Control-Request-Method": "GET"})
        assert r.headers["access-control-allow-origin"] in ("*", "http://localhost:5173")


def test_vitals_have_range_countdown_left_and_strip_endpoint_and_beta_blocker():
    svc, sms, io = make("fall", speed=8.0)
    with TestClient(create_app(svc)) as c:
        assert wait_for(lambda: svc.engine.alerts.pending_countdown, 40)
        v = svc.engine.vitals
        assert len(v["hr_range"]) == 2 and v["hr_range"][0] < v["hr_range"][1]
        cd = c.get("/api/status").json()["alerts"]["countdown"][0]
        assert 0 < cd["countdown_left_s"] <= 30
        ev = svc.store.list("events")[0]
        s = c.get(f"/api/events/{ev['id']}/strip").json()
        assert s["fs"] > 50 and len(s["samples"]) > 1000
        assert c.get("/api/events/nope/strip").status_code == 404
        c.post("/api/imok")
        lo_before = svc.engine.vitals["hr_range"][0]
        c.put("/api/patient", json={"medications": ["Metoprolol 25 mg"]})
        assert svc.engine.beta_blocker is True
        wait_for(lambda: svc.engine.vitals["hr_range"][0] != lo_before, 10)
        assert svc.engine.vitals["hr_range"][0] < lo_before + 1


def test_websocket_event_messages_keep_their_type_tag():
    """Regression: alert fields must never overwrite the message discriminator `t`."""
    svc, _, _ = make("fall", speed=8.0)
    with TestClient(create_app(svc)) as c, c.websocket_connect("/ws/live") as ws:
        for _ in range(3000):
            m = ws.receive_json()
            if m.get("why") == "raised":
                assert m["t"] == "event" and m["kind"] == "fall" and "raised_t" in m
                break
        else:
            raise AssertionError("no event message seen")


def test_pair_advertises_the_configured_port():
    cfg = make_settings(port=9123)
    svc = EdgeService(cfg, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(":memory:"))
    with TestClient(create_app(svc, autostart=False)) as c:
        p = c.get("/api/pair").json()
        assert p["port"] == 9123 and p["qr"].endswith(":9123") and all(u.endswith(":9123") for u in p["urls"])

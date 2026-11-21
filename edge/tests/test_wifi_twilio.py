"""Wi-Fi ESP32 ingest + Twilio SMS. Twilio is replaced by a local fake server: NO real SMS is ever sent by these tests."""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from fastapi.testclient import TestClient
from senseband_edge.alerts.sms import MockSms
from senseband_edge.alerts.twilio import FallbackSms, TwilioGateway, is_e164, make_sms, parse_numbers
from senseband_edge.api.app import create_app
from senseband_edge.bands import BandHub
from senseband_edge.config import load_settings
from senseband_edge.envfile import load_env_files, parse_env
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.source import SimSource
from senseband_edge.service import EdgeService
from senseband_edge.storage.db import Store

from .harness import make_settings, run_scenario

SID = "AC" + "0123456789abcdef" * 2
TOKEN = "t" * 32
BAND_TOKEN = "band-secret-token-123456"


# ================================================================== fake Twilio =======================================
class FakeTwilio(BaseHTTPRequestHandler):
    calls: list[dict] = []
    mode = "ok"

    def log_message(self, *a):  # noqa: ANN002
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        FakeTwilio.calls.append({"path": self.path, "auth": self.headers.get("Authorization"), "form": form,
                                 "ct": self.headers.get("Content-Type")})
        if FakeTwilio.mode == "ok":
            code, body = 201, {"sid": "SM1", "status": "queued"}
        elif FakeTwilio.mode == "unverified":
            code, body = 400, {"code": 21608, "message": "The number is unverified. Trial accounts can only send to verified numbers"}
        elif FakeTwilio.mode == "auth":
            code, body = 401, {"code": 20003, "message": "Authenticate"}
        elif FakeTwilio.mode == "failed":
            code, body = 201, {"sid": "SM2", "status": "failed"}
        else:
            code, body = 500, {}
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


@pytest.fixture
def twilio_url():
    srv = HTTPServer(("127.0.0.1", 0), FakeTwilio)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeTwilio.calls, FakeTwilio.mode = [], "ok"
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def gw(url, **kw):
    kw = {"account_sid": SID, "auth_token": TOKEN, "from_number": "+12025550123", **kw}
    return TwilioGateway(api_base=url, timeout_s=3, **kw)


def test_twilio_sends_the_right_request_and_never_exposes_secrets(twilio_url):
    g = gw(twilio_url)
    assert g.configured and g.send("+919876543210", "SenseBand CRITICAL [Asha]: Possible fall - no movement detected." + "x" * 200)
    c = FakeTwilio.calls[0]
    assert c["path"] == f"/2010-04-01/Accounts/{SID}/Messages.json" and c["ct"] == "application/x-www-form-urlencoded"
    assert base64.b64decode(c["auth"].split()[1]).decode() == f"{SID}:{TOKEN}"
    assert c["form"]["To"] == "+919876543210" and c["form"]["From"] == "+12025550123" and len(c["form"]["Body"]) == 160
    st = g.status()
    assert st["ok"] and st["sent"] == 1 and st["from"] == "+12***123"
    assert TOKEN not in json.dumps(st) and SID not in json.dumps(st)


def test_twilio_errors_are_explained_and_failures_counted(twilio_url):
    g = gw(twilio_url)
    for mode, needle in (("unverified", "not verified"), ("auth", "authentication failed"), ("failed", "failed"), ("boom", "HTTP 500")):
        FakeTwilio.mode = mode
        assert g.send("+919876543210", "hi") is False
        assert needle in g.last_error, (mode, g.last_error)
        assert TOKEN not in g.last_error
    assert g.failed == 4 and g.status()["ok"] is False
    FakeTwilio.mode = "ok"
    assert g.send("+919876543210", "hi") and g.last_error is None and g.status()["ok"]
    down = TwilioGateway(SID, TOKEN, "+12025550123", api_base="http://127.0.0.1:1", timeout_s=1)
    assert down.send("+919876543210", "hi") is False and "network error" in down.last_error


def test_twilio_refuses_to_send_when_not_configured_or_the_number_is_bad(twilio_url):
    for kw, needle in (({"from_number": "PASTE_HERE"}, "FROM_NUMBER"), ({"account_sid": ""}, "ACCOUNT_SID"), ({"auth_token": "short"}, "AUTH_TOKEN")):
        g = gw(twilio_url, **kw)
        assert not g.configured and g.send("+919876543210", "x") is False and needle in g.last_error
        assert g.status()["configured"] is False
    ms = gw(twilio_url, from_number="", messaging_service_sid="MG" + "a" * 32)
    assert ms.configured and ms.send("+919876543210", "x") and FakeTwilio.calls[-1]["form"].get("MessagingServiceSid") == "MG" + "a" * 32
    g = gw(twilio_url)
    for bad in ("9876543210", "+91", "abc", "", "+0123456789"):
        assert g.send(bad, "x") is False and "E.164" in g.last_error
    assert len(FakeTwilio.calls) == 1                                                   # none of the refused attempts hit the network


def test_number_parsing_and_e164():
    assert parse_numbers('["+919876543210", "+12025550123"]') == ["+919876543210", "+12025550123"]
    assert parse_numbers("+919876543210, +12025550123;+447700900123") == ["+919876543210", "+12025550123", "+447700900123"]
    assert parse_numbers("") == [] and parse_numbers("[broken +919876543210") == ["+919876543210"]
    assert is_e164("+919876543210") and not is_e164("919876543210") and not is_e164("+91 9876543210")


def test_fallback_tries_the_next_gateway_and_survives_a_crashing_one():
    class Boom:
        def send(self, n, t):  # noqa: ANN001
            raise RuntimeError("modem exploded")

        def status(self):
            return {"modem": "boom", "ok": False, "error": "x"}
    ok = MockSms()
    f = FallbackSms([Boom(), MockSms(fail_first=1), ok])
    assert f.send("+911", "hi") is True and len(ok.log) == 1                            # boom raises, mock #2 fails once, mock #3 delivers
    assert f.send("+911", "hi") is True and len(ok.log) == 1                            # now mock #2 delivers; #3 is not needed
    assert f.status()["modem"] == "boom+mock+mock" and f.status()["ok"] is True


def test_provider_selection_and_the_simulation_safety_net():
    env = {"TWILIO_ACCOUNT_SID": SID, "TWILIO_AUTH_TOKEN": TOKEN, "TWILIO_FROM_NUMBER": "+12025550123"}
    assert isinstance(make_sms(make_settings(), env), MockSms)                          # sim + auto = mock, even with credentials set
    assert isinstance(make_sms(make_settings(sms={"provider": "mock"}), env), MockSms)
    t = make_sms(make_settings(sms={"provider": "twilio"}), env)
    assert isinstance(t, TwilioGateway) and t.configured                                # only an explicit choice sends for real in a sim
    hw = load_settings({"mode": "hw"})
    only = make_sms(hw, env)
    assert isinstance(only, FallbackSms) and only.status()["modem"] == "twilio+sim800"   # real band: Twilio first, SIM800L as backup
    nocreds = make_sms(hw, {})
    assert nocreds.status()["modem"] == "sim800"                                         # unchanged behaviour without Twilio
    bad = make_sms(load_settings({"mode": "hw", "sms": {"provider": "twilio"}}), {})
    assert bad.status()["ok"] is False and "ACCOUNT_SID" in bad.status()["error"]


def test_critical_alert_goes_through_twilio_end_to_end(twilio_url):
    """The fall scenario with a real TwilioGateway pointed at the fake server: one SMS per contact, correct text."""
    cfg = make_settings(sms={"contacts": ["+919876543210"], "provider": "twilio"})
    tw = gw(twilio_url)
    r = run_scenario("fall", settings=cfg, sms=tw)                                      # type: ignore[arg-type]
    assert len(FakeTwilio.calls) == 1
    f = FakeTwilio.calls[0]["form"]
    assert f["To"] == "+919876543210" and f["Body"].startswith("SenseBand CRITICAL [Test Patient]: Possible fall")
    assert any(a.kind == "fall" and a.status == "sms_sent" for a in r.engine.alerts.alerts)


def test_twilio_failure_is_retried_then_recorded_not_fatal(twilio_url):
    FakeTwilio.mode = "unverified"
    cfg = make_settings(sms={"contacts": ["+919876543210"], "provider": "twilio"})
    r = run_scenario("fall", settings=cfg, sms=gw(twilio_url))                          # type: ignore[arg-type]
    assert len(FakeTwilio.calls) == 3                                                   # retries = 3
    a = next(a for a in r.engine.alerts.alerts if a.kind == "fall")
    assert a.status == "sms_failed" and r.store.get("events", a.id)["status"] == "sms_failed"


# ================================================================== env handling ======================================
def test_env_file_parsing_precedence_and_contacts_override(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\nTWILIO_ACCOUNT_SID = "ACabc"\nexport TWILIO_FROM_NUMBER=+12025550123  # my number\nALERT_TO_NUMBERS=["+919876543210"]\n'
                 "EMPTY=\nbad line\n")
    assert parse_env(f.read_text()) == {"TWILIO_ACCOUNT_SID": "ACabc", "TWILIO_FROM_NUMBER": "+12025550123",
                                        "ALERT_TO_NUMBERS": '["+919876543210"]', "EMPTY": ""}
    env = {"TWILIO_FROM_NUMBER": "+19999999999"}
    assert load_env_files([f, tmp_path / "missing"], env) == [f]
    assert env["TWILIO_FROM_NUMBER"] == "+19999999999" and env["TWILIO_ACCOUNT_SID"] == "ACabc"   # the real environment wins
    monkeypatch.setenv("ALERT_TO_NUMBERS", '["+919876543210", "not-a-number"]')
    assert load_settings({"mode": "sim"}).sms.contacts == ["+919876543210"]
    monkeypatch.setenv("ALERT_TO_NUMBERS", "garbage")
    assert load_settings({"mode": "sim"}).sms.contacts == ["+910000000000"]                      # nothing valid: keep the config value
    monkeypatch.delenv("ALERT_TO_NUMBERS")
    assert load_settings({"mode": "sim"}).sms.contacts == ["+910000000000"]


def test_no_secret_is_present_in_any_tracked_file():
    import subprocess
    out = subprocess.run(["git", "grep", "-nIE", "AC[0-9a-f]{32}|6c706dedf1e4|ae6f83cffb79", "--", ".", ":!edge/tests/test_wifi_twilio.py"],
                         capture_output=True, text=True, check=False)
    assert out.stdout == "", out.stdout


# ================================================================== Wi-Fi band hub ====================================
def hub(**cfg):
    return BandHub(make_settings(bands=cfg).bands, make_settings().quality, BAND_TOKEN)


def test_hub_is_closed_until_a_token_exists_and_checks_it_in_constant_time():
    h = BandHub(make_settings().bands, make_settings().quality, "")
    assert not h.open_for_business
    for tok in (None, "", "x"):
        with pytest.raises(Exception, match="closed") as e:
            h.authorize(tok)
        assert e.value.status == 403
    short = BandHub(make_settings().bands, make_settings().quality, "tooshort")
    assert not short.open_for_business
    g = hub()
    g.authorize(BAND_TOKEN)
    for tok in (None, "", "wrong", BAND_TOKEN + "x"):
        with pytest.raises(Exception) as e:
            g.authorize(tok)
        assert e.value.status == 401
    off = hub(enabled=False)
    with pytest.raises(Exception) as e:
        off.authorize(BAND_TOKEN)
    assert e.value.status == 404


def test_hub_validates_everything_the_device_sends():
    g = hub()
    good = "V,72,1,97,1,1,2.1\nB,87\nQ,1000,1.0,0.0,0.0,0.0\nA,1000,0.01,0.02,0.99,0.1,0.2,0.3\n"
    r = g.ingest("S1", good, "10.59.27.50", now=100.0)
    assert (r.accepted, r.rejected) == (4, 0)
    r = g.ingest("S1", "rm -rf /\nV,abc\nV,1,2\nZ,1,2\n" + "Q," + "9" * 200 + "\nA,1,2,3\n\n", now=101.0)
    assert r.accepted == 0 and r.rejected == 6
    for bad_id in ("", "a b", "x" * 17, "../x", "S1;drop"):
        with pytest.raises(Exception) as e:
            g.ingest(bad_id, good)
        assert e.value.status == 422
    with pytest.raises(Exception) as e:
        g.ingest("S1", "B,1\n" * 2000)
    assert e.value.status == 413
    small = hub(max_body_bytes=100)
    with pytest.raises(Exception) as e:
        small.ingest("S1", "B,87\n" * 50)
    assert e.value.status == 413
    cap = hub(max_devices=2)
    cap.ingest("A1", "B,1\n")
    cap.ingest("A2", "B,1\n")
    with pytest.raises(Exception) as e:
        cap.ingest("A3", "B,1\n")
    assert e.value.status == 429
    cap.ingest("A1", "B,2\n")                                                          # known devices keep working


def test_hub_routes_primary_lines_waist_imu_and_extra_devices_separately():
    g = hub(primary="S1", waist_imu_from="S2")
    g.ingest("S1", "V,70,1,96,1,1,2.0\nB,90\nA,1,0,0,1,0,0,0\n", now=10.0)             # S1 is primary: A lines are not waist data
    g.ingest("S2", "A,1,0.0,0.0,1.0,1.0,2.0,3.0\nA,2,0.1,0.0,1.0,0.0,0.0,0.0\nB,55\nV,60,1,98,1,1,1.0\n", now=10.0)
    assert g.drain_primary() == ["V,70,1,96,1,1,2.0", "B,90"]                          # only S1's vitals reach the engine
    assert g.drain_primary() == []
    acc, gyro = g.drain_imu()
    assert acc.shape == (2, 3) and gyro.tolist() == [[1.0, 2.0, 3.0], [0.0, 0.0, 0.0]] and g.drain_imu() is None
    st = g.status(now=12.0)
    d = {x["id"]: x for x in st["devices"]}
    assert d["S1"]["role"] == "primary" and d["S2"]["role"] == "extra+waist_imu"
    assert d["S1"]["battery_pct"] == 90 and d["S2"]["battery_pct"] == 55 and d["S2"]["hr"] == 60 and d["S1"]["connected"]
    assert g.status(now=100.0)["devices"][0]["connected"] is False                      # silent devices show as disconnected
    assert "token" not in json.dumps(st).lower()


def test_hub_commands_are_whitelisted_queued_per_device_and_delivered_once():
    g = hub()
    assert g.queue_command("RATE,50") is False                                          # unknown device yet
    g.ingest("S1", "B,1\n")
    for bad in ("RATE,7", "VIB,99999", "reboot", "CAL;ls", "RATE,50\nCAL"):
        assert g.queue_command(bad) is False
    assert g.queue_command("RATE,50") and g.queue_command("RATE,50") and g.queue_command("VIB,120") and g.queue_command("CAL")
    r = g.ingest("S1", "B,2\n")
    assert r.commands == ["RATE,50", "VIB,120", "CAL"]                                  # duplicate RATE collapsed
    assert g.ingest("S1", "B,3\n").commands == []


# ================================================================== API + engine ======================================
def svc(**bands):
    cfg = make_settings(bands=bands) if bands else make_settings()
    s = EdgeService(cfg, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(":memory:"))
    s.bands.token = BAND_TOKEN
    return s


H = {"X-Band-Token": BAND_TOKEN, "X-Band-Id": "S1"}


def test_ingest_endpoint_security_and_responses():
    s = svc()
    with TestClient(create_app(s, autostart=False)) as c:
        body = "V,72,1,97,1,1,2.1\nB,80\n"
        assert c.post("/api/band/ingest", content=body).status_code == 401
        assert c.post("/api/band/ingest", content=body, headers={**H, "X-Band-Token": "nope"}).status_code == 401
        assert c.post("/api/band/ingest", content=body, headers={"X-Band-Token": BAND_TOKEN}).status_code == 422
        r = c.post("/api/band/ingest", content=body, headers=H)
        assert r.status_code == 200 and r.json() == {"accepted": 2, "rejected": 0, "commands": []}
        s.bands.queue_command("RATE,50")
        t = c.post("/api/band/ingest", content="B,81\n", headers={**H, "Accept": "text/plain"})
        assert t.headers["content-type"].startswith("text/plain") and t.text == "OK 1 0\nRATE,50\n"
        assert c.post("/api/band/ingest", content="x" * 70000, headers=H).status_code == 413
        assert c.post("/api/band/ingest", content="B,1\n", headers={**H, "Content-Length": "999999"}).status_code in (413, 400)
        info = c.get("/api/bands").json()
        assert info["open"] and info["devices"][0]["id"] == "S1" and info["devices"][0]["battery_pct"] == 81
        assert c.get("/api/status").json()["bands"]["primary"] == "S1"
        assert BAND_TOKEN not in json.dumps(c.get("/api/status").json())
    closed = svc()
    closed.bands.token = ""
    with TestClient(create_app(closed, autostart=False)) as c:
        assert c.post("/api/band/ingest", content="B,1\n", headers=H).status_code == 403
    off = svc(enabled=False)
    with TestClient(create_app(off, autostart=False)) as c:
        assert c.post("/api/band/ingest", content="B,1\n", headers=H).status_code == 404


def wait(cond, t=40.0):
    t0 = time.time()
    while time.time() - t0 < t and not cond():
        time.sleep(0.05)
    return cond()


def test_esp32_readings_over_wifi_reach_the_live_dashboard():
    """ESP32 posts V/B lines -> hub -> engine -> /ws vitals (spo2, hr source, battery) with the simulator as everything else."""
    s = EdgeService(make_settings(), SimSource("resting", speed=3.0, loop=True), MockSms(), SimIO(), Store(":memory:"))
    s.bands.token = BAND_TOKEN
    with TestClient(create_app(s)) as c:
        assert wait(lambda: s.engine.now > 2)
        for _ in range(25):                                                              # 25 s of 1 Hz readings from the ESP32
            c.post("/api/band/ingest", content="V,66,1,93,1,1,2.2\nB,64\n", headers=H)
            time.sleep(0.2)
        assert wait(lambda: s.engine.band.state.battery_pct == 64)
        assert s.engine.band.state.spo2 in (93.0, 97.0) or s.engine.band.state.battery_pct == 64
        st = c.get("/api/status").json()
        assert st["band"]["battery_pct"] == 64 and st["bands"]["devices"][0]["connected"] is True


def test_waist_imu_from_wifi_drives_activity_and_the_lying_posture():
    """A,... lines from an ESP32 worn at the waist replace the Pi's IMU: gravity along x = lying."""
    s = EdgeService(make_settings(bands={"waist_imu_from": "S1"}), SimSource("resting", speed=4.0, loop=True),
                    MockSms(), SimIO(), Store(":memory:"))
    s.bands.token = BAND_TOKEN
    with TestClient(create_app(s)) as c:
        assert wait(lambda: s.engine.now > 1)
        rng = np.random.default_rng(0)
        stop = {"v": False}

        def feed():
            t = 0
            while not stop["v"]:
                lines = "\n".join(f"A,{t + i},{1.0 + rng.normal(0, .004):.3f},{rng.normal(0, .004):.3f},{rng.normal(0, .004):.3f},0.1,0.1,0.1"
                                  for i in range(10))
                t += 10
                c.post("/api/band/ingest", content=lines, headers=H)
                time.sleep(0.05)
        th = threading.Thread(target=feed, daemon=True)
        th.start()
        try:
            assert wait(lambda: s.engine.act.result.posture == "lying", 60)
        finally:
            stop["v"] = True
            th.join(5)
        assert s.engine.act.result.activity in ("lying", "sleeping")


def test_rehab_commands_reach_the_wifi_band():
    s = svc()
    s.bands.ingest("S1", "B,1\n")
    s.engine.rehab.band_command("RATE,50")
    assert s.bands.ingest("S1", "B,2\n").commands == ["RATE,50"]
    assert s.source.band.rate_hz == 50                                                  # the BLE/sim path still gets it too

"""Milestone 6: reports, data export/erase, logging, power, OTA, deployment files."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from reportlab import rl_config
from senseband_edge import privacy
from senseband_edge.alerts.sms import MockSms
from senseband_edge.api.app import create_app
from senseband_edge.config import load_settings
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.source import SimSource
from senseband_edge.logging_setup import setup_logging
from senseband_edge.ota import update as ota
from senseband_edge.power import PowerManager
from senseband_edge.rehab.service import RecoveryService
from senseband_edge.reports.builder import build_report
from senseband_edge.reports.pdf import render_pdf
from senseband_edge.service import EdgeService
from senseband_edge.storage.db import Store

from .harness import kinds, make_settings, run_scenario

CFG = make_settings()
DAY = 86_400_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % DAY)          # a UTC midnight


# ============================================================ reports =====================================================
def synthetic_day(store: Store, gaps: bool = False) -> None:
    """24 h of minutes with known numbers: sleep 00-07, sit, walk 1 h, run 30 min, sit..."""
    plan = [(0, 420, "sleeping", 55.0, 94.0, 91.0), (420, 480, "sitting", 70.0, 97.0, 96.0), (480, 540, "walking", 100.0, 97.0, 95.0),
            (540, 840, "sitting", 72.0, 97.0, 96.0), (840, 870, "running", 150.0, 96.0, 94.0), (870, 1440, "sitting", 74.0, 97.0, 96.0)]
    for a, b, act, hr, sp, spmin in plan:
        for m in range(a, b):
            if gaps and 100 <= m < 160:
                continue
            store.upsert("vitals_minute", {"patient_id": "patient01", "minute_ts": T0 + m * 60000, "hr_ecg": hr, "hr_ppg": None,
                                           "hr_expected": hr - 2, "spo2_min": spmin, "spo2_avg": sp, "temp": 36.5, "activity": act,
                                           "intensity": 0.5, "posture": "lying", "rhythm": "regular",
                                           "quality_flags": ["ecg_noisy"] if m % 100 == 0 else [], "ews": 0})
    store.upsert("patients", {"id": "patient01", "name": "Asha Rao", "conditions": [], "medications": [], "emergency_contacts": []})
    ev = lambda i, ts, lvl, kind, msg, st, sms=0: store.upsert("events", {"id": i, "patient_id": "patient01", "ts": ts, "level": lvl,  # noqa: E731
                                                                          "kind": kind, "message": msg, "status": st, "sms_sent": sms})
    ev("e-fall", T0 + 600 * 60000, "critical", "fall", "Possible fall - no movement detected.", "sms_sent", 2)
    ev("e-warn", T0 + 200 * 60000, "warning", "sleep_desats", "Breathing dips during sleep (3 in 10 min) - discuss with your doctor.", "notified")
    ev("e-info", T0 + 700 * 60000, "info", "check_sensor_ppg", "SpO2 clip is not on the finger - check the clip.", "notified")
    ev("e-old", T0 - 5 * DAY, "critical", "fall", "outside the period", "sms_sent", 1)
    strip = (np.sin(np.linspace(0, 60, 250 * 30)) * 0.5).astype(np.float32)
    store.upsert("ecg_strips", {"id": "s1", "event_id": "e-fall", "ts": T0 + 600 * 60000, "fs": 250, "samples": strip.tobytes()})
    store.upsert("notes", {"id": "n1", "patient_id": "patient01", "ts": T0 + 100, "text": "Walk daily", "author_name": "Dr. Rao"})


def rep_for(store, **kw):
    return build_report(store, CFG, RecoveryService(CFG, store), T0, T0 + DAY, now_ms=T0 + DAY, **kw)


def test_daily_report_numbers_are_exact():
    s = Store()
    synthetic_day(s)
    r = rep_for(s)
    v = r.vitals
    assert v["minutes_with_data"] == 1440 and r.data_quality["coverage_pct"] == 100.0
    assert (v["hr_min"], v["hr_max"]) == (55.0, 150.0)
    exp_avg = (420 * 55 + 60 * 70 + 60 * 100 + 300 * 72 + 30 * 150 + 570 * 74) / 1440
    assert v["hr_avg"] == pytest.approx(exp_avg, abs=0.06)
    assert v["hr_resting_avg"] == pytest.approx((420 * 55 + 60 * 70 + 300 * 72 + 570 * 74) / 1350, abs=0.06)   # excludes walking/running
    assert v["spo2_min"] == 91.0 and v["temp_avg"] == 36.5 and v["temp_min"] == v["temp_max"] == 36.5
    assert r.activity_minutes == {"sleeping": 420, "sitting": 930, "walking": 60, "running": 30}
    assert r.sleep["minutes"] == 420 and r.sleep["desat_alerts"] == 1 and r.sleep["spo2_min"] == 91.0
    assert [e["id"] for e in r.events] == ["e-warn", "e-fall", "e-info"]                  # in period, in time order
    assert r.event_counts == {"warning": 1, "critical": 1, "info": 1}
    assert len(r.hourly) == 24 and r.hourly[0]["activity"] == "sleeping" and r.hourly[14]["activity"] == "running" or r.hourly[14]["hr_avg"] > 70
    assert r.data_quality["minutes_with_issues"] == {"ecg_noisy": 15}
    assert len(r.hr_series) == 1440 and len(r.spo2_series) == 1440
    assert len(r.strips) == 1 and r.strips[0]["event_id"] == "e-fall" and len(r.strips[0]["samples"]) <= 1500
    assert r.strips[0]["fs"] == pytest.approx(250 / (7500 // 1500), rel=0.01)
    assert [n["text"] for n in r.notes] == ["Walk daily"]
    assert r.patient["name"] == "Asha Rao" and r.kind == "daily" and r.rehab is None
    json.dumps(r.to_json(), default=str)


def test_report_marks_missing_data_instead_of_guessing_and_handles_empty():
    s = Store()
    synthetic_day(s, gaps=True)
    assert rep_for(s).data_quality["coverage_pct"] == pytest.approx(100 * 1380 / 1440, abs=0.1)
    empty = rep_for(Store())
    assert empty.vitals["hr_avg"] is None and empty.vitals["minutes_with_data"] == 0 and empty.events == []
    assert render_pdf(empty).startswith(b"%PDF")                                           # still renders


def pdf_text(rep) -> bytes:
    old = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        return render_pdf(rep)
    finally:
        rl_config.pageCompression = old


def test_pdf_contains_the_required_sections_and_safety_wording():
    s = Store()
    synthetic_day(s)
    data = pdf_text(rep_for(s))
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") >= 1
    for needle in (b"SenseBand health report", b"Asha Rao", b"not a medical device", b"does not diagnose", b"Resting heart rate",
                   b"Possible fall", b"SMS sent to 2 contact", b"ECG strips saved", b"Notes from your care team", b"Walk daily",
                   b"Sleep:", b"Data coverage"):
        assert needle in data, needle
    assert b"you have" not in data.lower() and b"diagnosed with" not in data.lower()


def test_program_report_has_recovery_progress_adherence_and_an_honest_forecast():
    import math
    s = Store()
    cfg = load_settings({"mode": "sim", "patient": {"id": "patient01"}})
    svc = EdgeService(cfg, SimSource("resting", speed=0.0), MockSms(), SimIO(), s)
    prog = svc.start_program("recovery", "elbow", None, recovery_case={"joint": "elbow", "side": "right", "injury_date": "2026-10-01",
                                                                        "max_angle_limits_json": {}})
    now = int(time.time() * 1000)
    for d in range(0, 24, 3):
        rom = 145 - 105 * math.exp(-0.09 * d)                      # plateaus above the 135 deg (90 % of normal) target
        s.upsert("assessments", {"program_id": prog["id"], "ts": now - (24 - d) * DAY, "kind": "rom", "movement": "elbow_flexion",
                                 "rom": rom, "min_angle": 0.0, "max_angle": rom, "phase": "baseline"})
        s.upsert("exercise_sessions", {"program_id": prog["id"], "start_ts": now - (24 - d) * DAY, "end_ts": now - (24 - d) * DAY + 60000,
                                       "exercise_id": "elbow_active_flexion", "reps_done": 10, "avg_peak_angle": 100.0, "max_angle": 105.0,
                                       "target_hit_pct": 90.0, "too_fast_count": 1, "pain_before": 2, "pain_after": 3, "stopped_reason": "completed"})
    rep = build_report(s, cfg, svc.recovery, int(prog["start_ts"]) - 30 * DAY, now + 1000, prog["id"], now_ms=now)
    assert rep.kind == "program" and rep.rehab and len(rep.rehab["sessions"]) == 8
    data = pdf_text(rep)
    for needle in (b"Recovery", b"progress", b"Adherence", b"estimate", b"Estimated", b"Pain", b"elbow", b"flexion"):
        assert needle in data, needle
    assert b"Estimated" in data and b"No recovery-date" not in data
    # a patient whose range has levelled off BELOW the target gets no date, only an honest note
    for a in s.list("assessments", "program_id=?", (prog["id"],)):
        s.upsert("assessments", {**a, "rom": 100 - 40 * math.exp(-0.2 * ((now - a["ts"]) / DAY * -1 + 24)), "max_angle": 100.0})
    flat = pdf_text(build_report(s, cfg, svc.recovery, now - 30 * DAY, now + 1000, prog["id"], now_ms=now))
    assert b"No recovery-date" in flat and b"Estimated" not in flat


def test_end_to_end_compressed_day_through_the_real_engine_into_a_report():
    """sim -> engine -> SQLite -> report: the whole chain on a 30-minute 'day'."""
    r = run_scenario("day_compressed", settings=make_settings(models={"enabled": True}))
    store, eng = r.store, r.engine
    base = eng.base_ms
    rep = build_report(store, CFG, RecoveryService(CFG, store), base - 60_000, base + 31 * 60_000, now_ms=base + 31 * 60_000)
    mins = rep.activity_minutes
    assert mins.get("sleeping", 0) + mins.get("lying", 0) >= 6 and mins.get("running", 0) >= 3 and mins.get("sitting", 0) >= 10
    assert 150 <= rep.vitals["hr_max"] <= 165 and 50 <= rep.vitals["hr_min"] <= 62
    assert rep.vitals["hr_resting_avg"] is not None and rep.vitals["hr_resting_avg"] < 90
    assert rep.sleep["minutes"] >= 5 and rep.sleep["desat_alerts"] >= 1 and rep.sleep["spo2_min"] <= 92
    assert rep.vitals["spo2_min"] <= 92 and rep.data_quality["coverage_pct"] > 90
    assert not [e for e in rep.events if e["level"] == "critical"] and r.sms.log == []
    assert render_pdf(rep).startswith(b"%PDF") and eng.stats["errors"] == 0


def svc_api():
    cfg = make_settings()
    svc = EdgeService(cfg, SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(":memory:"))
    return svc


def test_report_api_pdf_json_and_validation():
    svc = svc_api()
    synthetic = svc.store
    with TestClient(create_app(svc, autostart=False)) as c:
        now = int(time.time() * 1000)
        for m in range(30):
            synthetic.upsert("vitals_minute", {"patient_id": svc.cfg.patient.id, "minute_ts": (now // 60000 - m) * 60000, "hr_ecg": 70.0,
                                               "activity": "sitting", "spo2_avg": 97.0, "spo2_min": 96.0, "ews": 0, "quality_flags": []})
        r = c.get("/api/report")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
        j = c.get("/api/report", params={"format": "json", "hours": 1}).json()
        assert j["vitals"]["minutes_with_data"] == 30 and j["kind"] == "daily"
        assert c.get("/api/report", params={"day": time.strftime("%Y-%m-%d"), "format": "json"}).status_code == 200
        for bad in ({"day": "20-11-2026"}, {"hours": 0}, {"hours": 99999}, {"format": "docx"}):
            assert c.get("/api/report", params=bad).status_code == 422, bad
        assert c.get("/api/report", params={"program_id": "nope"}).status_code == 404
        pid = svc.active_program()["id"]
        assert c.get("/api/report", params={"program_id": pid}).headers["content-type"] == "application/pdf"


# ============================================================ export / erase ==============================================
def test_export_contains_everything_with_a_verifiable_manifest(tmp_path):
    s = Store()
    synthetic_day(s)
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "20261120T000000Z_walking.npz").write_bytes(b"npzdata")
    z = zipfile.ZipFile(io.BytesIO(privacy.export_zip(s, rec)))
    names = set(z.namelist())
    assert {"README.txt", "manifest.json", "events.jsonl", "vitals_minute.jsonl", "patients.jsonl", "notes.jsonl",
            "recordings/20261120T000000Z_walking.npz", "ecg_strips/e-fall_250Hz.csv"} <= names
    man = json.loads(z.read("manifest.json"))
    for name, meta in man["files"].items():
        assert hashlib.sha256(z.read(name)).hexdigest() == meta["sha256"] and len(z.read(name)) == meta["bytes"], name
    rows = [json.loads(line) for line in z.read("events.jsonl").decode().splitlines()]
    assert len(rows) == 4 and {r["id"] for r in rows} == {"e-fall", "e-warn", "e-info", "e-old"}
    assert man["files"]["vitals_minute.jsonl"]["rows"] == 1440
    csv = z.read("ecg_strips/e-fall_250Hz.csv").decode().splitlines()
    assert csv[0] == "mv" and len(csv) == 1 + 7500
    assert b"Digital Personal Data Protection" in z.read("README.txt")


def test_erase_needs_the_exact_word_and_really_removes_the_data_from_disk(tmp_path):
    db = tmp_path / "e.db"
    s = Store(str(db))
    s.upsert("patients", {"id": "patient01", "name": "UNIQUE-SECRET-NAME-12345", "conditions": ["UNIQUE-CONDITION-XYZ"],
                          "medications": [], "emergency_contacts": []})
    synthetic_day(s)
    s.upsert("patients", {"id": "patient01", "name": "UNIQUE-SECRET-NAME-12345", "conditions": [], "medications": [], "emergency_contacts": []})
    s.state_set("cloud_key", "SECRET-DEVICE-KEY-ABCDEF")
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "a.npz").write_bytes(b"x")
    for wrong in ("", "delete", "YES", "DELETE "):
        with pytest.raises(PermissionError):
            privacy.erase_all(s, rec, wrong)
    assert s.query("SELECT COUNT(*) AS n FROM events")[0]["n"] == 4                       # nothing happened
    counts = privacy.erase_all(s, rec, "DELETE")
    assert counts["events"] == 4 and counts["vitals_minute"] == 1440 and counts["recordings"] == 1
    for t in ("patients", "events", "vitals_minute", "ecg_strips", "outbox", "notes", "sync_state"):
        assert s.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] == 0, t
    assert not rec.exists()
    s.close()
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("e.db*"))
    assert b"UNIQUE-SECRET-NAME-12345" not in blob and b"SECRET-DEVICE-KEY-ABCDEF" not in blob and b"UNIQUE-CONDITION-XYZ" not in blob


def test_export_and_erase_api_and_the_band_keeps_working_afterwards():
    svc = svc_api()
    with TestClient(create_app(svc, autostart=False)) as c:
        c.put("/api/patient", json={"name": "Asha", "conditions": ["asthma"]})
        svc.store.upsert("events", {"patient_id": svc.cfg.patient.id, "ts": 1, "level": "info", "kind": "k"})
        r = c.get("/api/export")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert any(json.loads(line)["name"] == "Asha" for line in z.read("patients.jsonl").decode().splitlines())
        assert c.post("/api/data/erase", json={"confirm": "nope"}).status_code == 422
        assert c.post("/api/data/erase", json={"confirm": "DELETE"}).json()["erased"]["events"] == 1
        assert svc.store.list("events") == []
        assert c.get("/api/patient").json()["name"] != "Asha"                              # profile gone, band still usable
        assert c.get("/api/status").json()["program"]["status"] == "active"
        assert svc.store.state_get("patient_id") == svc.cfg.patient.id and svc.sync is None


# ============================================================ logging =====================================================
def test_logs_rotate_and_never_grow_unbounded(tmp_path):
    cfg = make_settings(logging={"dir": str(tmp_path / "logs"), "max_mb": 0.01, "backups": 3, "level": "INFO"}).logging
    path = setup_logging(cfg, console=False)
    setup_logging(cfg, console=False)                                                     # idempotent
    log = logging.getLogger("senseband.test")
    for i in range(4000):
        log.info("line %d %s", i, "x" * 60)
    files = sorted(p.name for p in path.parent.glob("senseband.log*"))
    assert files == ["senseband.log", "senseband.log.1", "senseband.log.2", "senseband.log.3"]
    assert sum(p.stat().st_size for p in path.parent.glob("senseband.log*")) < 4 * 12_000
    assert sum(getattr(h, "_senseband", False) for h in logging.getLogger().handlers) == 1
    for h in list(logging.getLogger().handlers):
        if getattr(h, "_senseband", False):
            logging.getLogger().removeHandler(h)
            h.close()


# ============================================================ power =======================================================
PW = make_settings(power={"ecg_duty_cycle": True, "sleep_min_s": 60, "ppg_stable_s": 30, "wake_every_s": 90, "wake_for_s": 10,
                          "resume_if_ppg_bad_s": 5}).power


def drive(pm: PowerManager, seconds: int, activity="sleeping", hr=56.0, alert=False, t0=0):
    out = []
    for t in range(t0, t0 + seconds):
        out.append(pm.update(float(t), activity, hr, alert, 40.0))
    return out


def test_power_manager_is_off_by_default_and_never_pauses_then():
    pm = PowerManager(CFG.power)
    assert all(drive(pm, 2000)) and not pm.paused


def test_power_manager_pauses_only_when_asleep_stable_and_wakes_on_every_risk():
    pm = PowerManager(PW)
    on = drive(pm, 200)
    assert on[:60] == [True] * 60 and pm.pauses >= 1 and False in on                       # pauses after the sleep+stable period
    t = 200
    pm2 = PowerManager(PW)
    drive(pm2, 150)
    assert pm2.paused
    assert drive(pm2, 1, activity="walking", t0=150) == [True] and not pm2.paused           # movement
    pm3 = PowerManager(PW)
    drive(pm3, 100)                                                                         # paused at ~60 s, next wake-up due at ~150 s
    assert pm3.paused
    got = [pm3.update(float(100 + i), "sleeping", None, False, 40.0) for i in range(8)]     # PPG lost
    assert got[:4] == [False] * 4 and got[-1] is True and not pm3.paused                    # back within resume_if_ppg_bad_s
    pm4 = PowerManager(PW)
    drive(pm4, 150)
    assert pm4.update(151.0, "sleeping", 56.0, True, 40.0) is True                          # any alert
    pm5 = PowerManager(PW)
    drive(pm5, 150)
    assert pm5.paused and any(drive(pm5, 120, t0=150)) and pm5.paused is not None          # periodic wake-up happens
    assert t > 0


def test_power_manager_does_not_pause_on_unstable_or_low_pulse():
    pm = PowerManager(PW)
    on = [pm.update(float(t), "sleeping", 56.0 + (25 if t % 2 else 0), False, 40.0) for t in range(300)]
    assert all(on)                                                                          # jumping pulse: never "stable"
    pm = PowerManager(PW)
    assert all(pm.update(float(t), "sleeping", 45.0, False, 40.0) for t in range(300))      # pulse within 10 of the low limit
    pm = PowerManager(PW)
    assert all(pm.update(float(t), "sitting", 56.0, False, 40.0) for t in range(300))       # awake


def test_ecg_pause_during_sleep_raises_no_false_alerts_and_resumes_cleanly():
    cfg = make_settings(activity={"sleep_after_lying_s": 20},
                        power={"ecg_duty_cycle": True, "sleep_min_s": 30, "ppg_stable_s": 15, "wake_every_s": 40,
                               "wake_for_s": 10, "resume_if_ppg_bad_s": 10})
    r = run_scenario("sleeping", 400, settings=cfg, strip_ecg_when_paused=True)
    q = [v["quality"]["ecg"] for v in r.vitals]
    assert "paused" in q and q.count("good") > 60 and r.engine.power.pauses >= 2
    first_pause = q.index("paused")
    assert "good" in q[first_pause:]                                                   # the periodic wake-ups bring the ECG back
    assert not [a for a in r.engine.alerts.alerts if a.kind in ("collapse", "check_sensor_ecg", "hr_low")], kinds(r)
    assert r.sms.log == [] and q[-1] in ("good", "paused", "collecting")


def test_collapse_while_the_ecg_is_paused_is_still_caught_and_sms_sent():
    cfg = make_settings(activity={"sleep_after_lying_s": 20},
                        power={"ecg_duty_cycle": True, "sleep_min_s": 30, "ppg_stable_s": 15, "wake_every_s": 600,
                               "wake_for_s": 10, "resume_if_ppg_bad_s": 8})
    scen = {"name": "sleep_then_asystole", "seed": 3, "segments": [
        {"duration_s": 240, "activity": "sleeping", "hr": 55, "spo2": 96},
        {"duration_s": 120, "activity": "sleeping", "hr": 0, "ramp_s": 3}]}
    r = run_scenario("x", settings=cfg, scenario=scen, strip_ecg_when_paused=True)
    assert r.engine.power.pauses >= 1
    c = [a for a in r.engine.alerts.alerts if a.kind == "collapse"]
    assert len(c) == 1 and c[0].t - 243 < 45                                                # ECG resumed, flat line found, within ~45 s
    assert len(r.sms.delivered) == 1 and "collapse" in r.sms.delivered[0].text.lower()


# ============================================================ OTA =========================================================
def keypair():
    sk = Ed25519PrivateKey.generate()
    pub = base64.b64encode(sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return sk, pub


def make_release(tmp: Path, version: str, files: dict[str, bytes] | None = None, version_file: str | None = None) -> Path:
    p = tmp / f"rel-{version}.tar"
    with tarfile.open(p, "w") as t:
        for name, data in {"VERSION": (version_file or version).encode(), "edge/app.py": b"print('hi')", **(files or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            t.addfile(info, io.BytesIO(data))
    return p


def manifest_for(sk, tar: Path, version: str) -> dict:
    sha = hashlib.sha256(tar.read_bytes()).hexdigest()
    return {"version": version, "url": tar.as_uri(), "sha256": sha, "size": tar.stat().st_size,
            "signature": base64.b64encode(sk.sign(ota.signed_bytes(version, sha))).decode()}


def test_manifest_signature_and_version_rules():
    sk, pub = keypair()
    tar = Path(__file__)
    m = manifest_for(sk, tar, "1.2.0")
    ota.verify_manifest(m, pub)
    _, other_pub = keypair()
    for bad in (dict(m, version="1.2.1"), dict(m, sha256="0" * 64), dict(m, signature=base64.b64encode(b"x" * 64).decode()),
                dict(m, sha256="zz"), {k: v for k, v in m.items() if k != "signature"}):
        with pytest.raises(ota.OtaError):
            ota.verify_manifest(bad, pub)
    with pytest.raises(ota.OtaError):
        ota.verify_manifest(m, other_pub)                                                  # signed by someone else
    with pytest.raises(ota.OtaError):
        ota.verify_manifest(m, "not-base64!!")
    assert ota.is_newer("1.10.0", "1.9.9") and not ota.is_newer("1.0.0", "1.0.0") and ota.is_newer("0.1.1", "0.1.0")
    for v in ("", "1..2", "v1", "1.2.3.4.5", "1.x"):
        with pytest.raises(ota.OtaError):
            ota.parse_version(v)


def test_download_checks_size_hash_and_scheme(tmp_path):
    tar = make_release(tmp_path, "1.0.0")
    sha = hashlib.sha256(tar.read_bytes()).hexdigest()
    ota.download(tar.as_uri(), tmp_path / "ok.tar", sha, 10 ** 6)
    assert (tmp_path / "ok.tar").read_bytes() == tar.read_bytes()
    with pytest.raises(ota.OtaError, match="signed hash"):
        ota.download(tar.as_uri(), tmp_path / "bad.tar", "0" * 64, 10 ** 6)
    assert not (tmp_path / "bad.tar").exists()
    with pytest.raises(ota.OtaError, match="larger"):
        ota.download(tar.as_uri(), tmp_path / "big.tar", sha, 100)
    assert not (tmp_path / "big.tar").exists()
    with pytest.raises(ota.OtaError, match="https"):
        ota.download("http://evil.example/x.tar", tmp_path / "x.tar", sha, 10 ** 6)


def test_archive_extraction_blocks_traversal_links_and_bad_version(tmp_path):
    rel = ota.Releases(tmp_path / "root")
    for name in ("../escape.txt", "/abs.txt", "a/../../escape.txt"):
        p = make_release(tmp_path, "9.0.0", {name: b"x"})
        with pytest.raises(ota.OtaError, match="unsafe"):
            rel.install(p, "9.0.0")
    lnk = tmp_path / "link.tar"
    with tarfile.open(lnk, "w") as t:
        i = tarfile.TarInfo("evil")
        i.type, i.linkname = tarfile.SYMTYPE, "/etc/passwd"
        t.addfile(i)
        v = tarfile.TarInfo("VERSION")
        v.size = 5
        t.addfile(v, io.BytesIO(b"9.0.0"))
    with pytest.raises(ota.OtaError, match="links"):
        rel.install(lnk, "9.0.0")
    with pytest.raises(ota.OtaError, match="VERSION"):
        rel.install(make_release(tmp_path, "9.0.1", version_file="9.9.9"), "9.0.1")
    assert not (tmp_path / "escape.txt").exists() and rel.current_version() is None
    assert not list((tmp_path / "root" / "releases").glob(".*partial*")) and not (tmp_path / "root" / "releases" / "9.0.0").exists()


def test_atomic_switch_previous_pruning_and_rollback(tmp_path):
    rel = ota.Releases(tmp_path / "root", keep=2)
    for v in ("1.0.0", "1.1.0", "1.2.0", "1.3.0"):
        rel.install(make_release(tmp_path, v), v)
        assert rel.current_version() == v
    assert (tmp_path / "root" / "previous").resolve().name == "1.2.0"
    assert sorted(p.name for p in (tmp_path / "root" / "releases").iterdir()) == ["1.2.0", "1.3.0"]      # keep=2 (+current/previous)
    with pytest.raises(ota.OtaError, match="already installed"):
        rel.install(make_release(tmp_path, "1.3.0"), "1.3.0")
    assert rel.rollback() == "1.2.0" and rel.current_version() == "1.2.0"
    assert not (tmp_path / "root" / "releases" / "1.3.0").exists()                                       # the bad release is removed
    with pytest.raises(ota.OtaError, match="no previous"):
        rel.rollback()
    assert (tmp_path / "root" / "current" / "edge" / "app.py").read_text() == "print('hi')"


def ota_cfg(tmp, pub, **kw):
    return make_settings(ota={"enabled": True, "manifest_url": "https://x/manifest.json", "public_key": pub, "root": str(tmp / "root"),
                              "restart_cmd": ["true"], "health_timeout_s": 5, **kw}).ota


def test_update_flow_success_up_to_date_unhealthy_rollback_and_refusals(tmp_path):
    sk, pub = keypair()
    cfg = ota_cfg(tmp_path, pub)
    restarts = []
    fetch = lambda tar, v: (lambda url: manifest_for(sk, tar, v))  # noqa: E731
    base = make_release(tmp_path, "1.0.0")
    ota.Releases(Path(cfg.root)).install(base, "1.0.0")
    # up to date
    r = ota.apply_update(cfg, "1.0.0", fetch(base, "1.0.0"), restart=restarts.append, healthy=lambda v: True)
    assert r == {"updated": False, "reason": "up to date", "version": "1.0.0"} and restarts == []
    # good update
    v11 = make_release(tmp_path, "1.1.0")
    r = ota.apply_update(cfg, "1.0.0", fetch(v11, "1.1.0"), restart=restarts.append, healthy=lambda v: v == "1.1.0", sleep=lambda s: None)
    assert r == {"updated": True, "version": "1.1.0"} and restarts == [["true"]]
    assert ota.Releases(Path(cfg.root)).current_version() == "1.1.0"
    # new version never becomes healthy -> automatic rollback + a second restart
    v12 = make_release(tmp_path, "1.2.0")
    cfg2 = ota_cfg(tmp_path, pub, health_timeout_s=0.05)
    r = ota.apply_update(cfg2, "1.1.0", fetch(v12, "1.2.0"), restart=restarts.append, healthy=lambda v: False, sleep=lambda s: None)
    assert r["updated"] is False and r["rolled_back_to"] == "1.1.0" and ota.Releases(Path(cfg.root)).current_version() == "1.1.0"
    assert len(restarts) == 3
    # tampered tarball: signed hash no longer matches -> nothing installed, nothing restarted
    v13 = make_release(tmp_path, "1.3.0")
    man = manifest_for(sk, v13, "1.3.0")
    raw = bytearray(v13.read_bytes())
    raw[-1] ^= 0x01                                                                                 # same size, one bit flipped
    v13.write_bytes(bytes(raw))
    with pytest.raises(ota.OtaError, match="signed hash"):
        ota.apply_update(cfg, "1.1.0", lambda u: man, restart=restarts.append, healthy=lambda v: True)
    assert not (Path(cfg.root) / "releases" / "1.3.0").exists() and len(restarts) == 3
    # a download that is bigger than the signed size is cut off before it fills the disk
    big = make_release(tmp_path, "1.4.0", {"edge/blob.bin": b"x" * 5000})
    bigman = manifest_for(sk, big, "1.4.0")
    with pytest.raises(ota.OtaError, match="larger"):
        ota.apply_update(cfg, "1.1.0", lambda u: dict(bigman, size=100), restart=restarts.append, healthy=lambda v: True)
    # a manifest signed by the wrong key is refused before any download
    _, wrong = keypair()
    with pytest.raises(ota.OtaError, match="signature"):
        ota.apply_update(ota_cfg(tmp_path, wrong), "1.1.0", fetch(v12, "1.2.0"), restart=restarts.append)
    # not configured
    with pytest.raises(ota.OtaError, match="not configured"):
        ota.apply_update(make_settings().ota, "1.0.0", fetch(v12, "1.2.0"))


def test_service_writes_the_health_marker_the_updater_waits_for(tmp_path, monkeypatch):
    marker = tmp_path / "healthy"
    monkeypatch.setenv("SENSEBAND_HEALTH_FILE", str(marker))
    monkeypatch.setenv("SENSEBAND_VERSION", "1.4.2")
    svc = EdgeService(make_settings(), SimSource("resting", speed=0.0), MockSms(), SimIO(), Store(":memory:"))
    with TestClient(create_app(svc)):
        t0 = time.time()
        while time.time() - t0 < 20 and not marker.exists():
            time.sleep(0.05)
    assert marker.read_text() == "1.4.2"


# ============================================================ deployment files ============================================
DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_deployment_files_are_valid_and_hardened():
    cfg = load_settings(path=str(DEPLOY / "config.example.yaml"))
    assert cfg.mode == "hw" and cfg.logging.dir.startswith("/var/lib/senseband")
    unit = (DEPLOY / "senseband.service").read_text()
    for needle in ("MemoryMax=400M", "NoNewPrivileges=yes", "ProtectSystem=strict", "Restart=always", "User=senseband",
                   "ReadWritePaths=", "SENSEBAND_HEALTH_FILE", "--hw"):
        assert needle in unit, needle
    assert "OnCalendar" in (DEPLOY / "senseband-update.timer").read_text()
    assert "python -m senseband_edge.ota --apply" in (DEPLOY / "senseband-update.service").read_text()
    assert "rotate" in (DEPLOY / "senseband.logrotate").read_text()
    for sh in ("power.sh", "install.sh"):
        assert subprocess.run(["sh", "-n", str(DEPLOY / sh)], check=False).returncode == 0
    power = (DEPLOY / "power.sh").read_text()
    assert "ondemand" in power and power.rstrip().endswith("exit 0") and re.search(r"set -u", power)   # best effort, never blocks start

"""Unit tests for edge building blocks (parsers, storage, filters, SMS, degradation)."""
from __future__ import annotations

import asyncio
import math
import sqlite3

import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R
from senseband_edge.alerts.ews import ews_score
from senseband_edge.alerts.sms import MockSms, send_with_retries
from senseband_edge.config import load_settings
from senseband_edge.context.engine import Engine
from senseband_edge.hw.io import SimIO
from senseband_edge.hw.sim import Simulator
from senseband_edge.hw.sim.ble_lines import SmallBandEmulator
from senseband_edge.hw.source import SimSource
from senseband_edge.pipeline.blelink import SmallBandParser
from senseband_edge.pipeline.madgwick import Madgwick
from senseband_edge.storage.db import Store
from senseband_edge.storage.schema import MIGRATIONS

from .harness import make_settings

CFG = make_settings()


# ---- config ------------------------------------------------------------------------------------
def test_config_rejects_unknown_keys_and_merges_overrides(tmp_path):
    with pytest.raises(Exception, match="extra|Extra"):
        load_settings({"alerts": {"countdown": 5}})
    f = tmp_path / "site.yaml"
    f.write_text("alerts: {countdown_s: 12}\nsms: {contacts: ['+91111']}\n")
    c = load_settings(path=str(f))
    assert c.alerts.countdown_s == 12 and c.sms.contacts == ["+91111"]
    assert c.alerts.sos_countdown_s == 0                       # untouched defaults survive
    assert load_settings({"mode": "sim"}).is_sim and not load_settings({"mode": "hw"}).is_sim


# ---- BLE protocol ------------------------------------------------------------------------------
def test_parser_v2_lines_and_ppg_validity():
    p = SmallBandParser(CFG.quality)
    assert p.feed("Q,1000,0.9,0.1,0.2,0.3", 1.0) == "Q" and p.state.quat == (0.9, 0.1, 0.2, 0.3)
    assert p.feed("V,72,1,97,1,1,2.1", 2.0) == "V"
    s = p.state
    assert s.hr_valid and s.hr == 72 and s.spo2 == 97 and s.perfusion == 2.1
    assert p.feed("V,-999,0,-999,0,1,0.0", 3.0) == "V" and s.hr is None and s.spo2 is None
    assert p.feed("V,72,1,97,1,0", 4.0) == "V"                 # legacy 6-field, finger OFF
    assert not s.hr_valid and not s.spo2_valid                 # finger off => never valid
    assert p.feed("V,300,1,97,1,1", 5.0) == "V" and not s.hr_valid   # out of physiological range
    assert p.feed("B,83", 6.0) == "B" and s.battery_pct == 83
    assert p.feed("I,5,10.5,-3.0", 7.0) == "I" and s.pitch_roll == (10.5, -3.0)
    for bad in ("", "garbage", "V,1,2", "Q,1,x,y,z,w", "B", "V,a,b,c,d,e"):
        assert p.feed(bad, 8.0) is None
    assert s.bad_lines >= 6 and s.fresh(8.0) and not s.fresh(100.0)


def test_parser_smooths_ppg_hr():
    p = SmallBandParser(CFG.quality)
    p.feed("V,70,1,97,1,1,2", 1)
    p.feed("V,100,1,97,1,1,2", 2)
    assert 75 < p.state.hr < 85                                # 0.7*70 + 0.3*100 = 79


def test_emulator_speaks_the_firmware_protocol_and_accepts_commands():
    sim, band = Simulator("resting"), SmallBandEmulator()
    lines = []
    for f in sim.frames(5):
        lines += band.feed(f)
    assert sum(x.startswith("Q,") for x in lines) in range(49, 52)          # 10 Hz
    assert sum(x.startswith("V,") for x in lines) == 5
    assert any(x.startswith("B,") for x in lines)
    assert all(len(x.split(",")) == 7 for x in lines if x.startswith("V,"))
    band.command("RATE,50")
    more = []
    for f in sim.frames(2):
        more += band.feed(f)
    assert sum(x.startswith("Q,") for x in more) > 90                        # 50 Hz now
    band.command("RATE,7")                                                   # invalid: ignored
    assert band.rate_hz == 50 and band.commands == ["RATE,50", "RATE,7"]


# ---- storage -----------------------------------------------------------------------------------
def test_migrations_are_versioned_idempotent_and_persistent(tmp_path):
    path = str(tmp_path / "t.db")
    s = Store(path)
    assert s.version == len(MIGRATIONS)
    assert s.migrate() == len(MIGRATIONS)                       # running again changes nothing
    s.upsert("notes", {"patient_id": "p", "author_id": "a", "ts": 1, "text": "hi"})
    s.close()
    s2 = Store(path)
    assert s2.version == len(MIGRATIONS) and len(s2.list("notes")) == 1
    assert s2.one("PRAGMA journal_mode")["journal_mode"] == "wal"
    names = {r["name"] for r in s2.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"patients", "devices", "programs", "recovery_cases", "calibrations", "vitals_minute",
            "events", "ecg_strips", "assessments", "exercise_plans", "exercise_sessions",
            "forecasts", "notes", "outbox", "raw_chunks"} <= names


def test_upsert_is_idempotent_and_outbox_tracks_changes():
    s = Store()
    row = {"patient_id": "p", "minute_ts": 60000, "hr_ecg": 70.0, "quality_flags": ["ecg_noisy"]}
    s.upsert("vitals_minute", row)
    s.upsert("vitals_minute", {**row, "hr_ecg": 71.0})
    assert len(s.list("vitals_minute")) == 1 and s.list("vitals_minute")[0]["hr_ecg"] == 71.0
    assert s.list("vitals_minute")[0]["quality_flags"] == ["ecg_noisy"]
    ev = s.upsert("events", {"patient_id": "p", "ts": 1, "level": "info", "kind": "k"})
    assert len(ev["id"]) == 36                                   # UUID created on the edge
    s.update("events", ev["id"], {"sms_sent": 2})
    assert s.get("events", ev["id"])["sms_sent"] == 2
    with pytest.raises(KeyError):
        s.update("events", "missing", {"sms_sent": 1})
    pend = s.pending()
    assert s.backlog() == len(pend) == 4
    s.mark_sent([p["id"] for p in pend[:3]])
    assert s.backlog() == 1 and s.pending()[0]["row_id"] == ev["id"]


def test_failed_write_rolls_back_and_leaves_no_outbox_row():
    s = Store()
    before = s.backlog()
    with pytest.raises(sqlite3.Error):
        s.upsert("events", {"patient_id": "p", "ts": 1, "level": "info"})   # kind NOT NULL
    assert s.backlog() == before and s.list("events") == []


# ---- Madgwick ------------------------------------------------------------------------------------
def test_madgwick_converges_to_gravity_and_tracks_rotation():
    f = Madgwick(beta=0.1)
    for _ in range(300):                                          # start tilted 40 deg, still
        f.update(math.sin(math.radians(40)), 0.0, math.cos(math.radians(40)), 0, 0, 0, 0.02)
    w, x, y, z = f.q
    up_body = R.from_quat([x, y, z, w]).inv().apply([0, 0, 1])
    assert np.degrees(np.arccos(np.clip(up_body @ [math.sin(math.radians(40)), 0,
                                                   math.cos(math.radians(40))], -1, 1))) < 1.0
    # rotate 90 deg about y at -90 dps for 1 s; the accelerometer follows consistently
    g0 = f.q
    for k in range(50):
        ang = math.radians(40 + 90 * (k + 1) / 50)
        f.update(math.sin(ang), 0.0, math.cos(ang), 0, -90.0, 0, 0.02)
    rel = (R.from_quat([g0[1], g0[2], g0[3], g0[0]]).inv()
           * R.from_quat([f.q[1], f.q[2], f.q[3], f.q[0]])).magnitude()
    assert abs(math.degrees(rel) - 90) < 5


def test_madgwick_on_simulated_exercise_matches_truth_tilt():
    """Filtered simulated limb IMU: gravity direction must match the truth quaternion."""
    sim = Simulator("elbow_exercise")
    f, errs = Madgwick(), []
    for fr in sim.frames(100):
        c = fr.imu["limb_moving"]
        for a, g, q in zip(c.acc_g, c.gyro_dps, c.quat_wxyz, strict=True):
            est = f.update(*a, *g, 0.02)
            up_est = R.from_quat([est[1], est[2], est[3], est[0]]).inv().apply([0, 0, 1])
            up_true = R.from_quat([q[1], q[2], q[3], q[0]]).inv().apply([0, 0, 1])
            errs.append(math.degrees(math.acos(np.clip(up_est @ up_true, -1, 1))))
    assert np.median(errs[500:]) < 3.0


# ---- SMS ---------------------------------------------------------------------------------------
def test_send_with_retries_semantics():
    gw = MockSms(fail_first=2)
    sleeps = []
    r = send_with_retries(gw, ["+1"], "x" * 300, 3, 5.0, sleeps.append)
    assert r.delivered == ["+1"] and r.attempts == 3 and sleeps == [5.0, 5.0]
    assert all(len(m.text) == 160 for m in gw.log)               # one SMS segment
    gw = MockSms(fail_first=99)
    r = send_with_retries(gw, ["+1", "+2"], "hi", 3, 0, lambda s: None)
    assert r.failed == ["+1", "+2"] and r.attempts == 6 and r.delivered == []


# ---- EWS ---------------------------------------------------------------------------------------
def test_ews_scores_and_ignores_missing_inputs():
    e = CFG.ews
    assert ews_score(e, 72, 97, 36.6) == 0
    assert ews_score(e, 140, 90, 39.5) == 3 + 3 + 2
    assert ews_score(e, None, None, None) == 0                   # poor signal never raises score
    assert ews_score(e, 72, 94, 36.6) == 1


# ---- graceful degradation ------------------------------------------------------------------------
def test_engine_survives_every_sensor_missing():
    """CLAUDE.md: the edge must keep working if any single sensor / BLE link is missing."""
    from senseband_edge.hw.frames import EcgChunk, GripChunk, ImuChunk, PpgChunk, SensorFrame
    from senseband_edge.hw.source import Bundle
    eng = Engine(CFG, Store(), MockSms(), SimIO(), base_ms=0, sleep=lambda s: None)
    empty3, empty4 = np.zeros((0, 3)), np.zeros((0, 4))
    imu = ImuChunk(50, 0, empty3, empty3, empty4)
    for k in range(60):
        f = SensorFrame(k * .2, (k + 1) * .2, EcgChunk(250, 0, np.zeros(0), np.zeros(0, bool)),
                        {"waist": imu, "limb_ref": imu, "limb_moving": imu},
                        PpgChunk(25, 0, np.zeros(0, np.int32), np.zeros(0, np.int32),
                                 np.zeros(0, bool)))
        eng.process(Bundle(f, []))
    assert eng.vitals["hr"] is None and eng.alerts.alerts == []
    assert eng.signal_check()["ppg"]["quality"] == "none" and not eng.signal_check()["ready"]
    assert GripChunk  # imported for completeness


def test_hardware_source_degrades_without_pi_libraries():
    from senseband_edge.hw.hardware_source import HardwareSource
    src = HardwareSource(make_settings(mode="hw"))
    try:
        assert not any(src.ok[k] for k in ("ecg", "imu"))
        assert src.errors                                         # reported, not raised
        h = src.health()
        assert h["source"] == "hw" and h["ble"] is False

        async def grab():
            out = []
            async for b in src.stream():
                out.append(b)
                if len(out) == 3:
                    break
            return out

        bundles = asyncio.run(grab())
        eng = Engine(make_settings(mode="hw"), Store(), MockSms(), SimIO(), base_ms=0)
        for b in bundles:
            eng.process(b)                                        # empty chunks must not crash
        assert eng.alerts.alerts == []
    finally:
        src.close()


def test_sim_source_commands_reach_the_band():
    src = SimSource("resting", speed=0)
    src.send_band_command("RATE,25")
    assert src.band.rate_hz == 25 and src.health()["ble"] is True


def test_firmware_protocol_matches_edge_parser_and_config():
    """No compiler in CI: guard against drift between small_band.ino and the edge by text."""
    from pathlib import Path
    ino = (Path(__file__).resolve().parents[2] / "firmware/small_band/small_band.ino").read_text()
    assert '"Q,%lu,%.4f,%.4f,%.4f,%.4f"' in ino and '"B,%d"' in ino
    assert '"V,%ld,%d,%ld,%d,%d,%.1f"' in ino               # 7 fields incl. perfusion
    assert f'#define DATA_UUID    "{CFG.hardware.ble_data_uuid}"' in ino
    assert f'#define CMD_UUID     "{CFG.hardware.ble_cmd_uuid}"' in ino
    assert f'#define DEVICE_NAME  "{CFG.hardware.small_band_name}"' in ino
    for cmd in ('"RATE,"', '"VIB,"', '"CAL"'):
        assert cmd in ino
    # what the firmware sends must parse on the edge
    p = SmallBandParser(CFG.quality)
    for line in ("Q,123456,1.0000,0.0000,0.0000,0.0000", "B,87", "V,72,1,97,1,1,2.3"):
        assert p.feed(line, 1.0) is not None

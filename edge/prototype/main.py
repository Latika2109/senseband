#!/usr/bin/env python3
"""SenseBand MAIN band – runs on the Raspberry Pi 3.

Threads:
  ecg_loop     250 Hz ECG sampling (and grip sampling during a grip test)
  imu_loop     50 Hz IMU: orientation, activity, fall detection, joint angle
  vitals_loop  every 5 s: heart rate / rhythm, temperature, alert rules, logging
  cloud_loop   uploads stored data whenever internet is available
  BLE          receives SpO2 / HR / IMU from the small band
  Flask        dashboard at http://<pi-ip>:8080
"""
import math
import os
import threading
import time
from collections import deque

import numpy as np
from flask import Flask, jsonify, request, send_file

import config as cfg
from comms import SIM800, Cloud, SmallBandLink
from processing import (Baseline, ECGAnalyzer, FallDetector, Orientation, RomTracker,
                        suggest_exercises)
from sensors import ADS1115, MPU6050, TempSensor
from storage import Store

lock = threading.Lock()
state = {
    "mode": "monitor",              # "monitor" (waist) or "rehab" (on the limb)
    "hr_ecg": None, "irregularity": None, "ecg_status": "starting", "lead_off": True,
    "temp": None,
    "hr_ppg": None, "spo2": None, "finger": False,
    "small_pitch": None, "small_last": 0.0,
    "main_pitch": None, "activity": 0.0,
    "rom_angle": None, "rom_active": False, "rom_result": None,
    "grip_active": False, "grip_result": None,
    "online": False,
}

store = Store(cfg.DB_PATH)
sim = SIM800(cfg.SIM_PORT, cfg.SIM_BAUD)
cloud = Cloud(cfg.CLOUD_URL, cfg.CLOUD_TOKEN, cfg.PATIENT_ID)
ecg = ECGAnalyzer(cfg.ECG_HZ)
rom = RomTracker()
grip = {"active": False, "samples": []}


# ---------------------------------------------------------------- hardware
def init_hw():
    global bus, mpu, ads, temp, lo_plus, lo_minus
    bus = mpu = ads = temp = lo_plus = lo_minus = None
    try:
        from smbus2 import SMBus
        bus = SMBus(cfg.I2C_BUS)
    except Exception as e:
        print("I2C not available:", e)
    if bus:
        try:
            mpu = MPU6050(bus, cfg.MPU_ADDR)
            print("MPU6050 OK – calibrating gyro, keep the band still...")
            mpu.calibrate_gyro()
        except Exception as e:
            print("MPU6050 not found:", e)
            mpu = None
        try:
            ads = ADS1115(bus, cfg.ADS_ADDR)
            print("ADS1115 OK")
        except Exception as e:
            print("ADS1115 not found:", e)
            ads = None
    if cfg.TEMP_SENSOR:
        temp = TempSensor(cfg.TEMP_SENSOR, bus, cfg.MLX_ADDR, cfg.TEMP_OFFSET)
    try:
        from gpiozero import DigitalInputDevice, DigitalOutputDevice
        lo_plus = DigitalInputDevice(cfg.PIN_LO_PLUS, pull_up=False)
        lo_minus = DigitalInputDevice(cfg.PIN_LO_MINUS, pull_up=False)
        if cfg.PIN_ECG_SDN is not None:
            init_hw.sdn = DigitalOutputDevice(cfg.PIN_ECG_SDN, initial_value=True)  # ECG on
    except Exception as e:
        print("GPIO not available (lead-off detection disabled):", e)


def leads_off():
    if lo_plus is None:
        return False
    return bool(lo_plus.value or lo_minus.value)


# ---------------------------------------------------------------- alerts
class Alerts:
    def __init__(self):
        self.last = {}
        self.recent = deque(maxlen=20)

    def raise_(self, level, kind, msg):
        now = time.time()
        if now - self.last.get(kind, 0) < cfg.SMS_COOLDOWN_S:
            return
        self.last[kind] = now
        print(f"ALERT [{level}] {msg}")
        row = {"ts": now, "level": level, "kind": kind, "message": msg}
        self.recent.appendleft(row)
        store.add("alerts", row)
        online = cloud.online()
        if (level == "critical" and (cfg.ALWAYS_SMS_CRITICAL or not online)) or \
           (level == "warning" and not online):
            sim.send_all_async(cfg.SMS_CONTACTS,
                               f"SenseBand [{cfg.PATIENT_NAME}] {level.upper()}: {msg}")
        if online:
            threading.Thread(target=cloud.push, args=(store,), daemon=True).start()


alerts = Alerts()


# ---------------------------------------------------------------- small band data
def on_small_line(line):
    p = line.split(",")
    try:
        if p[0] == "I" and len(p) == 4:
            with lock:
                state["small_pitch"] = float(p[2])
                state["small_last"] = time.time()
        elif p[0] == "V" and len(p) == 6:
            hr, hrv, sp, spv, fing = map(int, p[1:])
            with lock:
                state["finger"] = bool(fing)
                if not fing:
                    state["hr_ppg"] = state["spo2"] = None
                    return
                if hrv and 30 < hr < 220:          # smooth the noisy PPG heart rate
                    old = state["hr_ppg"]
                    state["hr_ppg"] = hr if old is None else round(0.7 * old + 0.3 * hr, 1)
                if spv and 70 <= sp <= 100:
                    state["spo2"] = sp
    except ValueError:
        pass


link = SmallBandLink(cfg.SMALL_BAND_NAME, cfg.DATA_CHAR_UUID, on_small_line)


# ---------------------------------------------------------------- loops
def ecg_loop():
    period = 1.0 / cfg.ECG_HZ
    nxt = time.perf_counter()
    chunk, was_off = [], True
    while True:
        nxt += period
        try:
            if grip["active"]:
                grip["samples"].append(ads.volts(cfg.GRIP_CHANNEL))
                was_off = True
            elif leads_off():
                if not was_off:
                    ecg.clear()
                was_off = True
                chunk = []
                state["lead_off"] = True
            else:
                if was_off:
                    ecg.clear()
                    was_off = False
                state["lead_off"] = False
                chunk.append(ads.volts(cfg.ECG_CHANNEL))
                if len(chunk) >= cfg.ECG_HZ // 5:
                    ecg.add_chunk(chunk)
                    chunk = []
        except OSError as e:
            print("ADS1115 read error:", e)
            time.sleep(0.1)
        d = nxt - time.perf_counter()
        if d > 0:
            time.sleep(d)
        else:
            nxt = time.perf_counter()      # fell behind – resync


def imu_loop():
    ori = Orientation()
    fall = FallDetector(cfg)
    mags = deque(maxlen=100)
    last = time.perf_counter()
    while True:
        t0 = time.perf_counter()
        dt, last = t0 - last, t0
        try:
            ax, ay, az, gx, gy, gz = mpu.read()
        except OSError:
            time.sleep(0.05)
            continue
        pitch, _ = ori.update(ax, ay, az, gx, gy, dt)
        amag = math.sqrt(ax * ax + ay * ay + az * az)
        mags.append(amag)
        now = time.time()
        with lock:
            state["main_pitch"] = round(pitch, 1)
            state["activity"] = round(float(np.std(mags)), 3)
            mode = state["mode"]
            sp, fresh = state["small_pitch"], now - state["small_last"] < 1.0
        if mode == "monitor":
            ev = fall.update(amag, now)
            if ev == "fall":
                alerts.raise_("critical", "fall",
                              "Possible FALL detected and person not moving. Check immediately.")
        if sp is not None and fresh:
            ang = rom.angle(pitch, sp)
            rom.update(ang, now)
            state["rom_angle"] = round(ang, 1)
        else:
            state["rom_angle"] = None
        time.sleep(max(0.0, 0.02 - (time.perf_counter() - t0)))


def check_vitals(s, bl):
    resting = s["activity"] < cfg.ACTIVITY_REST
    hr = s["hr_ecg"] or s["hr_ppg"]
    if hr:
        if hr < cfg.HR_LOW:
            alerts.raise_("critical", "hr_low", f"Heart rate LOW: {hr:.0f} bpm")
        elif resting and hr > cfg.HR_HIGH_REST:
            alerts.raise_("critical", "hr_high_rest", f"Heart rate HIGH at rest: {hr:.0f} bpm")
        elif hr > cfg.HR_HIGH_ACTIVE:
            alerts.raise_("critical", "hr_high", f"Heart rate very HIGH: {hr:.0f} bpm")
        if resting:      # learn the personal baseline only at rest
            z = bl["hr"].check(hr)
            if z is not None and abs(z) > cfg.BASELINE_Z:
                alerts.raise_("warning", "hr_unusual",
                              f"Heart rate {hr:.0f} bpm is unusual for this person")
    if s["ecg_status"] == "ok" and s["irregularity"] and s["irregularity"] > cfg.RR_IRREGULARITY:
        alerts.raise_("warning", "rhythm", "Irregular heart rhythm detected on ECG")
    if s["spo2"] and s["finger"]:
        if s["spo2"] < cfg.SPO2_CRIT:
            alerts.raise_("critical", "spo2_crit", f"Blood oxygen LOW: {s['spo2']}%")
        elif s["spo2"] < cfg.SPO2_WARN:
            alerts.raise_("warning", "spo2_low", f"Blood oxygen below normal: {s['spo2']}%")
    if s["temp"] is not None:
        if cfg.TEMP_HIGH and s["temp"] > cfg.TEMP_HIGH:
            alerts.raise_("warning", "fever", f"High temperature: {s['temp']} C")
        if cfg.TEMP_LOW and s["temp"] < cfg.TEMP_LOW:
            alerts.raise_("warning", "temp_low", f"Low temperature: {s['temp']} C")


def vitals_loop():
    bl = {"hr": Baseline()}
    last_store = 0.0
    while True:
        time.sleep(5)
        res = ecg.analyze() if ads and not state["lead_off"] else None
        t = temp.read() if temp else None
        with lock:
            if res:
                state.update(hr_ecg=res["hr"], irregularity=res["irregularity"],
                             ecg_status=res["status"])
            elif state["lead_off"]:
                state.update(hr_ecg=None, irregularity=None, ecg_status="leads off")
            state["temp"] = t
            snap = dict(state)
        if state["mode"] == "monitor":
            check_vitals(snap, bl)
        if time.time() - last_store >= 30:
            last_store = time.time()
            store.add("vitals", {"ts": last_store, "hr_ecg": snap["hr_ecg"],
                                 "hr_ppg": snap["hr_ppg"], "spo2": snap["spo2"],
                                 "temp": snap["temp"], "irregularity": snap["irregularity"],
                                 "activity": snap["activity"]})


def cloud_loop():
    while True:
        state["online"] = cloud.online()
        cloud.push(store)
        time.sleep(cfg.CLOUD_PUSH_EVERY_S)


# ---------------------------------------------------------------- grip test
def run_grip_test(hand):
    grip["samples"] = []
    grip["active"] = state["grip_active"] = True
    state["grip_result"] = None
    time.sleep(cfg.GRIP_TEST_S)
    grip["active"] = state["grip_active"] = False
    v = np.asarray(grip["samples"])
    if v.size == 0:
        state["grip_result"] = {"error": "no samples – check ADS1115"}
        return
    if cfg.GRIP_SENSOR == "fsr":
        xs, ys = zip(*cfg.GRIP_CAL)
        vals, unit = np.interp(v, xs, ys), "kg"
    else:
        vals, unit = np.abs(v), "V (relative)"
    top = np.sort(vals)[-max(1, len(vals) // 5):]          # strongest 20 % of the squeeze
    res = {"ts": time.time(), "hand": hand, "peak": round(float(vals.max()), 1),
           "mean": round(float(top.mean()), 1), "unit": unit}
    store.add("grip_tests", res)
    state["grip_result"] = res


# ---------------------------------------------------------------- web
app = Flask(__name__)


@app.get("/")
def index():
    return send_file(os.path.join(cfg.HERE, "dashboard.html"))


@app.get("/api/state")
def api_state():
    with lock:
        s = dict(state)
    s["small_connected"] = link.connected
    s["alerts"] = list(alerts.recent)
    s["ecg_wave"] = ecg.wave() if not s["lead_off"] else []
    s["rom_offset"] = round(rom.offset, 1)
    return jsonify(s)


@app.post("/api/mode")
def api_mode():
    m = (request.json or {}).get("mode")
    if m in ("monitor", "rehab"):
        state["mode"] = m
    return jsonify(mode=state["mode"])


@app.post("/api/rom/calibrate")
def api_rom_cal():
    with lock:
        mp, sp, fresh = state["main_pitch"], state["small_pitch"], \
            time.time() - state["small_last"] < 1.0
    if mp is None or sp is None or not fresh:
        return jsonify(ok=False, error="Both bands must be connected"), 400
    rom.calibrate(mp, sp)
    return jsonify(ok=True, offset=round(rom.offset, 1))


@app.post("/api/rom/start")
def api_rom_start():
    rom.joint = (request.json or {}).get("joint", "elbow")
    rom.reset()
    rom.active = state["rom_active"] = True
    state["mode"] = "rehab"
    state["rom_result"] = None
    return jsonify(ok=True)


@app.post("/api/rom/stop")
def api_rom_stop():
    rom.active = state["rom_active"] = False
    summ = rom.summary()
    if not summ:
        return jsonify(ok=False, error="No movement recorded"), 400
    prev = store.query("SELECT rom FROM rom_sessions WHERE joint=? ORDER BY ts DESC LIMIT 1",
                       (summ["joint"],))
    g = store.query("SELECT hand, peak FROM grip_tests WHERE unit='kg' ORDER BY ts DESC LIMIT 10")
    grip_kg = g[0]["peak"] if g else None
    ref = next((r["peak"] for r in g[1:] if g and r["hand"] != g[0]["hand"]), None)
    summ["ts"] = time.time()
    store.add("rom_sessions", summ)
    sugg = suggest_exercises(summ, prev[0]["rom"] if prev else None, grip_kg, ref)
    result = {**summ, "suggestions": sugg}
    state["rom_result"] = result
    if not cloud.online():          # no internet -> recovery update by SMS
        chg = sugg.get("change_since_last")
        txt = (f"SenseBand recovery [{cfg.PATIENT_NAME}] {summ['joint']}: ROM {summ['rom']} deg "
               f"({sugg.get('rom_pct')}% of normal), {summ['reps']} reps"
               + (f", {chg:+} deg vs last" if chg is not None else ""))
        sim.send_all_async(cfg.SMS_CONTACTS, txt)
    return jsonify(result)


@app.post("/api/grip")
def api_grip():
    if not ads:
        return jsonify(ok=False, error="ADS1115 not connected"), 400
    if grip["active"]:
        return jsonify(ok=False, error="Test already running"), 400
    hand = (request.json or {}).get("hand", "right")
    threading.Thread(target=run_grip_test, args=(hand,), daemon=True).start()
    return jsonify(ok=True, seconds=cfg.GRIP_TEST_S)


@app.get("/api/history")
def api_history():
    return jsonify(
        vitals=store.query("SELECT * FROM vitals ORDER BY ts DESC LIMIT 30"),
        rom=store.query("SELECT * FROM rom_sessions ORDER BY ts DESC LIMIT 30"),
        grip=store.query("SELECT * FROM grip_tests ORDER BY ts DESC LIMIT 30"),
        alerts=store.query("SELECT * FROM alerts ORDER BY ts DESC LIMIT 30"))


# ---------------------------------------------------------------- start
if __name__ == "__main__":
    init_hw()
    if ads:
        threading.Thread(target=ecg_loop, daemon=True).start()
    if mpu:
        threading.Thread(target=imu_loop, daemon=True).start()
    threading.Thread(target=vitals_loop, daemon=True).start()
    threading.Thread(target=cloud_loop, daemon=True).start()
    link.start()
    print(f"Dashboard: http://<this-pi-ip>:{cfg.WEB_PORT}")
    app.run(host="0.0.0.0", port=cfg.WEB_PORT, threaded=True)

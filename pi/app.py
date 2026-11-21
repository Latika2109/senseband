"""SenseBand Pi app."""
import json
import math
import os
import socket
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np
from flask import Flask, jsonify, redirect, render_template, request
from scipy.signal import butter, find_peaks, sosfiltfilt

import config

UDP_PORT = 5005
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "senseband.db")
DT = 0.02
IMU_FS = 50
ECG_FS = 120
FINGER_MIN_IR = 10000

app = Flask(__name__)
lock = threading.Lock()


def db():
    return sqlite3.connect(DB)


def init_db():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY, name TEXT, age INTEGER, "
                  "contact TEXT, reason TEXT, joint TEXT, side TEXT)")


def load_profile():
    with db() as c:
        r = c.execute("SELECT name,age,contact,reason,joint,side FROM profile WHERE id=1").fetchone()
    return dict(zip(["name", "age", "contact", "reason", "joint", "side"], r)) if r else None


init_db()
profile = load_profile()


def is_rehab():
    return bool(profile and profile["reason"] == "Rehab")


S = dict(main_seen=0, sup_seen=0, temp=None, sos=0, hr=None, spo2=None, status="NORMAL", sleeping=False,
         alert=None, last_alert=0, last_vitals=0, still_since=None, last_impact=0, angle=None, jmin=None, jmax=None)
ir_buf, red_buf, ecg_buf = deque(maxlen=300), deque(maxlen=300), deque(maxlen=600)
events = deque(maxlen=50)


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def log(text):
    events.appendleft(f"{now_str()}  {text}")


class Imu:
    def __init__(self):
        self.pitch = None
        self.g = np.array([0.0, 0.0, 1.0])
        self.recent = deque(maxlen=50)
        self.ref = np.array([0.0, 0.0, 1.0])
        self.ref_pitch = 0.0

    def update(self, samples):
        for ax, ay, az, gx, gy, gz in samples:
            acc_pitch = math.degrees(math.atan2(ax, az))
            if self.pitch is None:
                self.pitch = acc_pitch
                self.g = np.array([ax, ay, az])
            else:
                self.pitch = 0.98 * (self.pitch + gy * DT) + 0.02 * acc_pitch
                self.g = 0.9 * self.g + 0.1 * np.array([ax, ay, az])
            self.recent.append((ax, ay, az))

    def zero(self):
        if self.recent:
            m = np.mean(self.recent, axis=0)
            self.ref = m
            self.ref_pitch = math.degrees(math.atan2(m[0], m[2]))

    def tilt(self):
        a, b = self.g, self.ref
        d = np.linalg.norm(a) * np.linalg.norm(b)
        return 0.0 if d == 0 else math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b) / d)))))


main_imu, sup_imu = Imu(), Imu()


def send_alerts(msg):
    c = config
    placeholder = ("PASTE_HERE" in (c.TWILIO_ACCOUNT_SID, c.TWILIO_AUTH_TOKEN, c.TWILIO_FROM_NUMBER)
                   or not c.ALERT_TO_NUMBERS or any("XXXX" in n for n in c.ALERT_TO_NUMBERS))
    if placeholder:
        print("[Twilio not configured] " + msg)
        log("alert printed (Twilio not configured)")
        return
    try:
        from twilio.rest import Client
        client = Client(c.TWILIO_ACCOUNT_SID, c.TWILIO_AUTH_TOKEN)
    except Exception as e:
        log(f"alert failed: {e}")
        return
    for n in c.ALERT_TO_NUMBERS:
        try:
            client.messages.create(body=msg, from_=c.TWILIO_FROM_NUMBER, to=n)
            log(f"SMS sent to {n}")
        except Exception as e:
            log(f"SMS failed to {n}: {e}")
    try:
        client.calls.create(twiml=f"<Response><Say>{msg}</Say></Response>",
                            from_=c.TWILIO_FROM_NUMBER, to=c.ALERT_TO_NUMBERS[0])
        log(f"call sent to {c.ALERT_TO_NUMBERS[0]}")
    except Exception as e:
        log(f"call failed: {e}")


def trigger_alert(event):
    """Show banner, log, and send SMS and call (max once per 30 s)."""
    S["alert"] = f"{event} at {now_str()}"
    log(f"ALERT: {event}")
    t = time.time()
    if t - S["last_alert"] < 30:
        log("SMS skipped (30 s limit)")
        return
    S["last_alert"] = t
    name = profile["name"] if profile else "Unknown"
    msg = (f"SenseBand alert. Patient {name}. Event {event}. Heart rate {S['hr'] or 'unknown'}, "
           f"SpO2 {S['spo2'] or 'unknown'}, temperature {S['temp'] or 'unknown'}. Time {now_str()}")
    threading.Thread(target=send_alerts, args=(msg,), daemon=True).start()


def bandpass(x, lo, hi, fs):
    return sosfiltfilt(butter(2, [lo, hi], btype="band", fs=fs, output="sos"), x)


def calc_vitals():
    if len(ir_buf) < 150:
        return
    i, r = np.array(ir_buf, float), np.array(red_buf, float)
    if i.mean() < FINGER_MIN_IR:
        S["hr"] = S["spo2"] = None
        return
    fi, fr = bandpass(i, 0.5, 4, IMU_FS), bandpass(r, 0.5, 4, IMU_FS)
    peaks, _ = find_peaks(fi, distance=int(0.3 * IMU_FS), prominence=0.5 * fi.std())
    if len(peaks) >= 2:
        S["hr"] = int(round(60 / (np.mean(np.diff(peaks)) / IMU_FS)))
    if fi.std() > 0 and r.mean() > 0:
        R = (fr.std() / r.mean()) / (fi.std() / i.mean())
        S["spo2"] = int(max(0, min(100, round(110 - 25 * R))))


def update_joint():
    if main_imu.pitch is None or sup_imu.pitch is None:
        return
    a = abs((sup_imu.pitch - sup_imu.ref_pitch) - (main_imu.pitch - main_imu.ref_pitch))
    S["angle"] = a
    S["jmin"] = a if S["jmin"] is None else min(S["jmin"], a)
    S["jmax"] = a if S["jmax"] is None else max(S["jmax"], a)


def fall_logic(imu, now):
    mags = [math.sqrt(s[0] ** 2 + s[1] ** 2 + s[2] ** 2) for s in imu]
    gyros = [math.sqrt(s[3] ** 2 + s[4] ** 2 + s[5] ** 2) for s in imu]
    if mags and max(mags) > config.IMPACT_G:
        S["last_impact"] = now
    still = bool(imu) and all(abs(m - 1) < 0.15 for m in mags) and all(g < 20 for g in gyros)
    if still:
        if S["still_since"] is None:
            S["still_since"] = now
    else:
        S["still_since"] = None
    still_for = now - S["still_since"] if S["still_since"] else 0
    impact = S["still_since"] is not None and S["last_impact"] >= S["still_since"] - 2
    if main_imu.tilt() < config.TILT_FALL_DEG:
        st = "NORMAL"
    elif impact and still_for >= 3:
        st = "FALL"
    elif S["sleeping"]:
        st = "NORMAL"
    elif still_for >= 5:
        st = "FALL"
    else:
        st = "POSSIBLE_FALL"
    if st == "FALL" and S["status"] != "FALL":
        trigger_alert("FALL detected")
    S["status"] = st


def handle_main(p):
    now = time.time()
    imu = p.get("imu") or []
    main_imu.update(imu)
    ecg_buf.extend(p.get("ecg") or [])
    if p.get("temp") is not None:
        S["temp"] = round(float(p["temp"]), 1)
    sos = int(p.get("sos", 0))
    if sos and not S["sos"]:
        trigger_alert("SOS button pressed")
    S["sos"] = sos
    S["main_seen"] = now
    if is_rehab():
        update_joint()
    else:
        fall_logic(imu, now)


def handle_sup(p):
    now = time.time()
    sup_imu.update(p.get("imu") or [])
    red_buf.extend(p.get("red") or [])
    ir_buf.extend(p.get("ir") or [])
    S["sup_seen"] = now
    if is_rehab():
        update_joint()
    if now - S["last_vitals"] >= 1:
        S["last_vitals"] = now
        calc_vitals()


def udp_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_PORT))
    while True:
        data, _ = sock.recvfrom(2048)
        try:
            p = json.loads(data)
            with lock:
                if p.get("d") == "main":
                    handle_main(p)
                elif p.get("d") == "sup":
                    handle_sup(p)
        except Exception as e:
            print("bad packet:", e)


@app.route("/")
def index():
    return redirect("/dashboard" if profile else "/setup")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    global profile
    if request.method == "POST":
        f = request.form
        with db() as c:
            c.execute("INSERT OR REPLACE INTO profile VALUES(1,?,?,?,?,?,?)",
                      (f["name"], int(f["age"] or 0), f["contact"], f["reason"], f.get("joint", ""), f.get("side", "")))
        with lock:
            profile = load_profile()
        return redirect("/guide")
    return render_template("setup.html", p=profile or {})


@app.route("/guide")
def guide():
    if not profile:
        return redirect("/setup")
    return render_template("guide.html", p=profile, rehab=is_rehab())


@app.route("/dashboard")
def dashboard():
    if not profile:
        return redirect("/setup")
    return render_template("dashboard.html", p=profile, rehab=is_rehab())


@app.route("/api/state")
def api_state():
    with lock:
        now = time.time()
        ecg_out = []
        if len(ecg_buf) >= 90:
            x = np.array(ecg_buf, float)
            ecg_out = np.round(bandpass(x - x.mean(), 0.5, 40, ECG_FS)[-360:], 1).tolist()
        a, lo, hi = S["angle"], S["jmin"], S["jmax"]
        return jsonify(
            rehab=is_rehab(), name=profile["name"] if profile else "",
            main_online=now - S["main_seen"] < 2, sup_online=now - S["sup_seen"] < 2,
            hr=S["hr"], spo2=S["spo2"], temp=S["temp"], ecg=ecg_out,
            tilt=round(main_imu.tilt(), 1), status=S["status"], sleeping=S["sleeping"],
            angle=None if a is None else round(a, 1), jmin=None if lo is None else round(lo, 1),
            jmax=None if hi is None else round(hi, 1),
            rom=None if lo is None else round(hi - lo, 1),
            alert=S["alert"], events=list(events)[:15])


@app.route("/api/zero", methods=["POST"])
def api_zero():
    with lock:
        main_imu.zero()
        if is_rehab():
            sup_imu.zero()
        S["jmin"] = S["jmax"] = S["angle"] = None
        log("Zero set (" + ("both IMUs" if is_rehab() else "main IMU") + ")")
    return jsonify(ok=True)


@app.route("/api/sos", methods=["POST"])
def api_sos():
    with lock:
        trigger_alert("SOS (dashboard button)")
    return jsonify(ok=True)


@app.route("/api/dismiss", methods=["POST"])
def api_dismiss():
    with lock:
        S["alert"] = None
    return jsonify(ok=True)


@app.route("/api/sleep", methods=["POST"])
def api_sleep():
    with lock:
        S["sleeping"] = bool((request.get_json(silent=True) or {}).get("on"))
        log("Sleeping mode " + ("ON" if S["sleeping"] else "OFF"))
    return jsonify(ok=True)


if __name__ == "__main__":
    threading.Thread(target=udp_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000)

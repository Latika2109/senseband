"""Communication: BLE link to small band, SMS via SIM800L, cloud upload."""
import asyncio
import json
import socket
import threading
import time
import urllib.request

import serial
from bleak import BleakClient, BleakScanner


# ---------------------------------------------------------------- BLE
class SmallBandLink:
    def __init__(self, name, char_uuid, on_line):
        self.name, self.uuid, self.on_line = name, char_uuid, on_line
        self.connected = False

    def start(self):
        threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True).start()

    async def _run(self):
        while True:
            try:
                dev = await BleakScanner.find_device_by_name(self.name, timeout=10)
                if dev is None:
                    continue
                async with BleakClient(dev) as client:
                    await client.start_notify(self.uuid, self._handler)
                    self.connected = True
                    print("Small band connected")
                    while client.is_connected:
                        await asyncio.sleep(1)
            except Exception as e:
                print("BLE:", e)
            if self.connected:
                print("Small band disconnected – retrying")
            self.connected = False
            await asyncio.sleep(2)

    def _handler(self, _sender, data):
        for line in data.decode(errors="ignore").strip().splitlines():
            self.on_line(line.strip())


# ---------------------------------------------------------------- SMS
class SIM800:
    def __init__(self, port, baud):
        self.lock = threading.Lock()
        try:
            self.ser = serial.Serial(port, baud, timeout=2)
            print("SIM800L:", "OK" if "OK" in self._cmd("AT") else "no reply (check power/wiring)")
        except Exception as e:
            print("SIM800L not available:", e)
            self.ser = None

    def _cmd(self, c, wait=1.0):
        self.ser.reset_input_buffer()
        self.ser.write((c + "\r").encode())
        time.sleep(wait)
        return self.ser.read(self.ser.in_waiting or 1).decode(errors="ignore")

    def send(self, number, text):
        if not self.ser:
            print("[SMS not sent – no modem]", number, text)
            return False
        with self.lock:
            self._cmd("AT+CMGF=1")                        # text mode
            self.ser.write(f'AT+CMGS="{number}"\r'.encode())
            time.sleep(0.5)
            self.ser.write(text[:160].encode() + b"\x1a")  # Ctrl+Z sends
            time.sleep(6)
            resp = self.ser.read(self.ser.in_waiting or 1).decode(errors="ignore")
        ok = "+CMGS" in resp
        print("SMS to", number, "OK" if ok else f"FAILED: {resp!r}")
        return ok

    def send_all_async(self, numbers, text):
        def job():
            for n in numbers:
                self.send(n, text)
        threading.Thread(target=job, daemon=True).start()


# ---------------------------------------------------------------- Cloud
class Cloud:
    def __init__(self, url, token, patient_id):
        self.url, self.token, self.pid = url, token, patient_id
        self._online, self._checked = False, 0.0

    def online(self):
        if time.time() - self._checked > 20:
            try:
                socket.create_connection(("8.8.8.8", 53), timeout=2).close()
                self._online = True
            except OSError:
                self._online = False
            self._checked = time.time()
        return self._online

    def push(self, store):
        """Upload everything waiting in the outbox. Data is never lost while offline."""
        if not self.url or not self.online():
            return
        rows = store.pending()
        if not rows:
            return
        body = {"patient": self.pid,
                "records": [{"kind": r["kind"], **json.loads(r["payload"])} for r in rows]}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    store.mark_sent([r["id"] for r in rows])
        except Exception as e:
            print("Cloud upload failed:", e)

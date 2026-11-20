# Run SenseBand on the real hardware

**Honest status:** the software has only been run on a laptop with the simulator. The Raspberry Pi drivers, the BLE link, the SIM800L, the
buzzer/LED/button and the ESP32 firmware have **never run on real hardware**, and the firmware has **never been compiled**. Expect to debug.
Work in this order and stop at the first step that fails. Pins and wiring: `docs/03_HARDWARE.md` (source of truth).

## 0. Try it without hardware first
```
git clone https://github.com/Latika2109/senseband && cd senseband
make setup && make test            # Python 3.11 + uv needed
make sim SCENARIO=fall             # open http://localhost:8080
```

## 1. Small band (ESP32) - flash the firmware
1. Arduino IDE -> Boards Manager: install **esp32 by Espressif**. Library Manager: **SparkFun MAX3010x**.
2. Open `firmware/small_band/small_band.ino`, board "ESP32 Dev Module" (D1 Mini) or your C3 board (C3 SuperMini: set `SDA_PIN 8`, `SCL_PIN 9`).
3. Wire per docs/03 (MAX30102 + MPU6050 on I2C 21/22, battery divider on GPIO34, optional vibration motor on GPIO25), upload.
4. Serial Monitor 115200 should print `MPU6050 OK`, `MAX30102 OK`, `BLE advertising as SenseBand-S`, then `Q,...`, `V,...`, `B,...` lines.
   If it fails to compile, fix it and tell us: it was written but never built.

## 2. Main band (Raspberry Pi 3, Raspberry Pi OS Lite 64-bit)
1. `sudo raspi-config` -> Interface Options: enable **I2C**, **Serial port hardware (disable login shell)**, **1-Wire**; reboot.
2. Wire per docs/03: MPU6050 + ADS1115 on I2C (GPIO2/3), AD8232 (LO+ GPIO17, LO- GPIO27, SDN GPIO22, output to ADS1115 A0), grip sensor to ADS1115 A1,
   DS18B20 on GPIO4, SIM800L on its **own 18650 + 1000 uF cap** (never from Pi pins) with the TX/RX divider, button GPIO23, buzzer GPIO24, LED GPIO25.
   **ECG only on battery, never while plugged into a mains charger.**
3. Bring-up checks (docs/03): `i2cdetect -y 1` shows 48 and 68; `ls /sys/bus/w1/devices` shows `28-...`; `AT` to the SIM800L answers OK;
   `bluetoothctl scan on` shows `SenseBand-S`.
4. Install: `git clone` the repo on the Pi, edit `edge/deploy/config.example.yaml` (**put your real emergency phone numbers**), then
   `sudo sh edge/deploy/install.sh`. The built app (`app/dist`) is already in the repo, so no Node is needed on the Pi.
   Models: `ml/models/` is already in the repo.
5. Check: `systemctl status senseband`, `journalctl -u senseband -f`, then `curl localhost:8080/api/status` (sensors, band link, SMS modem).
   `curl localhost:8080/api/models` shows which ONNX models are active. Quick manual run: `make edge`.

## 3. Phone / laptop app
Join the band's Wi-Fi (or your home Wi-Fi), open `http://<band-ip>:8080` (the band serves the app). First launch: profile -> Pair (auto-find or type the
address) -> program -> wear guide -> signal check -> live. Camera QR scanning needs a secure context in some browsers; typing the address always works.

## 4. Before you trust it
- Test the alert path on purpose: hold the button (SOS) and confirm the SMS arrives on your phone. Never rely on it before that.
- Calibrate the grip sensor (`hardware.grip_cal`) with known weights and set `hardware.temp_offset_c` against a thermometer.
- The local API has **no authentication**: use the band's own WPA2 hotspot or a trusted network only.
- Accuracy on people is **unmeasured**: follow `docs/07_VALIDATION_PROTOCOL.md` before any real use. SenseBand is a monitoring aid, not a medical device.
- Cloud sync is optional (`cloud/README.md`); the band works fully without it.

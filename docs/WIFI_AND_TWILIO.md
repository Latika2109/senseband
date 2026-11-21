# ESP32 over Wi-Fi + Twilio SMS - setup for the real system

Your layout: **Raspberry Pi at `10.59.27.40`**, two ESP32 small bands on the same Wi-Fi, an app on a phone/laptop on that network.

```
ESP32 S1 ─┐  HTTP POST every 0.5 s (token)         ┌── phone / laptop app
          ├──────────────► Raspberry Pi 10.59.27.40:8080 ──┤   http://10.59.27.40:8080
ESP32 S2 ─┘  SpO2, pulse, battery, orientation, IMU   └── Twilio SMS (critical alerts) + SIM800L backup (if fitted)
```
The ESP32s no longer use Bluetooth: they send the same readings (`Q`, `V`, `B`, `A` lines) to the Pi's `/api/band/ingest`, and the
Pi's pipeline (alerts, rules, models, storage) is unchanged. The app already talks to the Pi; just open `http://10.59.27.40:8080`.

## 1. Secrets (never in git)
The repo's `.env` (git-ignored, already created on your laptop) and, on the Pi, `/etc/senseband/senseband.env` (`chmod 640`) hold:
```
TWILIO_ACCOUNT_SID=AC...            TWILIO_AUTH_TOKEN=...            TWILIO_FROM_NUMBER=+1...   <- STILL MISSING
ALERT_TO_NUMBERS=["+91XXXXXXXXXX"]  SENSEBAND_BAND_TOKEN=<48 hex characters>
```
- **`TWILIO_FROM_NUMBER` is not set (it was `PASTE_HERE`).** Buy/choose a Twilio number (Console -> Phone Numbers) and put it in. Until then SMS
  is reported as "not configured" and **no SMS can be sent**.
- **Rotate the Twilio auth token now** (Console -> Account -> API keys & tokens -> Auth Token -> Rotate): it was pasted into a chat. Then update both files.
- Trial accounts can only text **verified** numbers (Console -> Verified Caller IDs): verify `+91XXXXXXXXXX`. For delivery in India, carriers
  may require DLT registration; if Twilio returns error 30007/21408 see Twilio's India guidelines.
- The simulator never sends real SMS (provider `auto` = mock in simulation), so `make sim SCENARIO=fall` is safe.

## 2. Raspberry Pi
```bash
git clone -b feature/wifi-esp32-twilio https://github.com/Latika2109/senseband.git && cd senseband
sudo sh edge/deploy/install.sh               # creates /etc/senseband/senseband.env with a NEW band token and prints it
sudo nano /etc/senseband/senseband.env        # add TWILIO_* and ALERT_TO_NUMBERS
sudo nano /etc/senseband/config.yaml          # bands.waist_imu_from: "S1" if an ESP32 is the waist IMU, "" if the Pi has its own MPU6050
sudo systemctl restart senseband

```
Check from any computer on the network:
```bash
curl http://10.59.27.40:8080/api/status | head -c 600           # sensors, "bands", "sms"
curl http://10.59.27.40:8080/api/bands                          # which ESP32s are connected
curl -X POST http://10.59.27.40:8080/api/band/ingest -H "X-Band-Id: TEST" -H "X-Band-Token: <token>" --data $'B,80\n'
```
SMS check (sends nothing without `--send`): `/opt/senseband/venv/bin/python -m senseband_edge.sms_test`

## 3. Each ESP32
1. Arduino IDE with the **esp32** board package and library **SparkFun MAX3010x**.
2. Open `firmware/small_band_wifi/small_band_wifi.ino`. Copy `secrets.example.h` to `secrets.h` in that folder and set `WIFI_SSID`, `WIFI_PASS`,
   `PI_HOST "10.59.27.40"`, `DEVICE_ID` (**S1** on the first, **S2** on the second), `BAND_TOKEN` (the Pi's token).
3. Upload. Serial Monitor (115200): `MPU6050 OK`, `MAX30102 OK`, `WiFi OK <ip>`. A `Pi refused the token` message means the token differs.
4. The first ESP32 (S1) is the *primary* band (SpO2/pulse/battery/joint orientation) and, if configured, the waist IMU for activity and fall
   detection. The second (S2) shows up in `/api/bands`; its data is accepted and counted but not yet used by the alert rules.
   Tell us what S2 should do and the rules can use it.
**The sketch has not been compiled yet** (no Arduino toolchain was available); fix any compile error and report it.

## 4. What reaches the app
S1's SpO2/pulse/battery -> the live dashboard, alerts and the 30 s "I'm OK" -> **SMS via Twilio** to `ALERT_TO_NUMBERS` (SIM800L is tried if Twilio fails
and a modem is fitted). Without an ECG front-end on the Pi there is no ECG-based heart rate, so pulse comes from the finger sensor and the collapse (flat ECG)
rule cannot trigger; fall detection needs a waist IMU (an ESP32 with `waist_imu_from`, or the Pi's own MPU6050).

## 5. Security
The ingest endpoint is closed until `SENSEBAND_BAND_TOKEN` is set and rejects anything without it; input is size- and format-checked. The rest of the Pi's API is
still unauthenticated: keep the Pi on a trusted Wi-Fi.

1. Flash `firmware/main_band.ino` and `firmware/support_band.ino` to the ESPs after setting `WIFI_SSID`, `WIFI_PASS` and `PI_IP` (libraries: OneWire, DallasTemperature, SparkFun MAX3010x).
2. On the Pi: `pip install flask numpy scipy twilio`, and paste your Twilio values and phone numbers into `pi/config.py`.
3. Run `python pi/app.py`.
4. Open `http://<pi-ip>:8000` and follow setup, guide, then dashboard.

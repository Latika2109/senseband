"""SenseBand main band – settings. Edit this file before running."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------- Patient ----------------
PATIENT_ID = "patient01"
PATIENT_NAME = "Test Patient"

# ---------------- SMS (SIM800L) ----------------
SMS_CONTACTS = ["+91XXXXXXXXXX"]   # family / doctor numbers, with country code
SIM_PORT = "/dev/serial0"
SIM_BAUD = 9600
SMS_COOLDOWN_S = 600               # same alert type is not repeated within 10 min
ALWAYS_SMS_CRITICAL = True         # critical alerts go by SMS even when internet is up

# ---------------- Cloud ----------------
# Any HTTPS endpoint that accepts a JSON POST (Firebase function, your own API,
# ThingSpeak bridge ...). Leave "" to keep everything local.
CLOUD_URL = ""
CLOUD_TOKEN = ""
CLOUD_PUSH_EVERY_S = 30

DB_PATH = os.path.join(HERE, "senseband.db")
WEB_PORT = 8080

# ---------------- Hardware ----------------
I2C_BUS = 1
MPU_ADDR = 0x68
ADS_ADDR = 0x48
ECG_CHANNEL = 0          # AD8232 OUTPUT -> ADS1115 A0
GRIP_CHANNEL = 1         # FSR / piezo   -> ADS1115 A1
ECG_HZ = 250
PIN_LO_PLUS = 17         # AD8232 LO+
PIN_LO_MINUS = 27        # AD8232 LO-
PIN_ECG_SDN = 22         # AD8232 SDN (None if tied to 3.3V)

TEMP_SENSOR = "ds18b20"  # "ds18b20", "mlx90614" or None
MLX_ADDR = 0x5A
TEMP_OFFSET = 0.0        # skin reads lower than body; set after comparing with a thermometer

GRIP_SENSOR = "fsr"      # "fsr" (gives kg) or "piezo" (relative only)
GRIP_TEST_S = 5
# FSR calibration: (volts at ADS1115, kg). REPLACE with your own measurements:
# squeeze the plier against a luggage scale / known weights and note the voltage.
GRIP_CAL = [(0.0, 0.0), (0.3, 1.0), (1.0, 5.0), (1.8, 15.0), (2.5, 30.0), (3.0, 45.0)]

# ---------------- Small band (BLE) ----------------
SMALL_BAND_NAME = "SenseBand-S"
DATA_CHAR_UUID = "5e75e000-0000-4000-8000-00000000b002"

# ---------------- Alert thresholds (prototype values, NOT clinically validated) ----
HR_LOW = 40
HR_HIGH_REST = 120       # when the person is not moving
HR_HIGH_ACTIVE = 190     # during sport / exercise
SPO2_WARN = 92
SPO2_CRIT = 88
TEMP_HIGH = 38.0
TEMP_LOW = None          # skin sensors read low; enable only after calibrating
RR_IRREGULARITY = 0.15   # beat-to-beat variation that counts as "irregular rhythm"
BASELINE_Z = 3.0         # "unusual for THIS person" = 3 std-devs from their own normal
ACTIVITY_REST = 0.08     # below this (g, std-dev) the person counts as resting

# ---------------- Fall detection (main band on waist) ----------------
FALL_FREEFALL_G = 0.4
FALL_IMPACT_G = 2.5
FALL_STILL_S = 10

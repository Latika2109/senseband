/*
 * SenseBand - small band over Wi-Fi (ESP32 + MAX30102 + MPU6050)
 *
 * Same sensors and messages as firmware/small_band (Bluetooth), but the readings go to the Raspberry Pi over the
 * local Wi-Fi network: every ~0.5 s the ESP32 POSTs a batch of text lines to  http://PI_HOST:PI_PORT/api/band/ingest
 *   Q,<ms>,<w>,<x>,<y>,<z>                       orientation quaternion (Madgwick), 10 Hz (25/50 Hz after RATE)
 *   V,<hr>,<hrValid>,<spo2>,<spo2Valid>,<finger>,<perfusion>   vitals, 1 Hz
 *   B,<battery_pct>                              battery, every 10 s
 *   A,<ms>,<ax>,<ay>,<az>,<gx>,<gy>,<gz>         raw IMU (g, deg/s) at 50 Hz  (if SEND_RAW_IMU)
 * The Pi's reply carries commands:  RATE,10|25|50   VIB,<ms>   CAL
 *
 * Setup: copy secrets.example.h to secrets.h and fill it in. Libraries: "SparkFun MAX3010x Pulse and Proximity
 * Sensor Library". Board: ESP32 Dev Module (D1 Mini) / for ESP32-C3 SuperMini set SDA_PIN 8, SCL_PIN 9.
 * Wiring: MAX30102 + MPU6050 on I2C (SDA 21, SCL 22, AD0-GND), battery divider 100k/100k to GPIO34,
 * optional vibration motor on GPIO25 via an NPN transistor.
 * NOTE: written but not yet compiled/flashed by the authors - fix any compile error you find and report it.
 */
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"
#include "secrets.h"

#define SDA_PIN 21
#define SCL_PIN 22
#define MPU_ADDR 0x68
#define BATT_PIN 34
#define VIB_PIN 25
#define MADGWICK_BETA 0.1f
#define FLUSH_MS 500
#define OUT_CAP 3600

MAX30105 ppg;
bool ppgOk = false, imuOk = false;

// ---- outgoing text buffer ------------------------------------------------------------------------------
char outBuf[OUT_CAP];
size_t outLen = 0;
unsigned long dropped = 0;
void emit(const char *line) {
  size_t n = strlen(line);
  if (outLen + n + 1 >= OUT_CAP) { dropped++; return; }     // link is down for too long: drop, never overflow
  memcpy(outBuf + outLen, line, n);
  outBuf[outLen + n] = '\n';
  outLen += n + 1;
}

// ---- PPG (100 samples @ 25 Hz = 4 s window, recalculated every 1 s) ------------------------------------
const int BUF = 100, STEP = 25;
uint32_t irBuf[BUF], redBuf[BUF];
int bufIdx = 0;
int32_t spo2 = 0, hr = 0;
int8_t spo2Valid = 0, hrValid = 0;
bool finger = false;
float perfusion = 0;

// ---- IMU -------------------------------------------------------------------------------------------------
float gbx = 0, gby = 0, gbz = 0;
float q0 = 1, q1 = 0, q2 = 0, q3 = 0;
unsigned long lastImu = 0, lastQ = 0, lastBatt = 0, lastFlush = 0, lastWifiTry = 0;
volatile int qRateHz = 10;
bool calRequested = false;
unsigned long vibUntil = 0;
bool vibOn = false;

void mpuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR); Wire.write(reg); Wire.write(val); Wire.endTransmission();
}
bool mpuRead(float &ax, float &ay, float &az, float &gx, float &gy, float &gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14) != 14) return false;
  int16_t r[7];
  for (int i = 0; i < 7; i++) { uint8_t hi = Wire.read(); uint8_t lo = Wire.read(); r[i] = (int16_t)((hi << 8) | lo); }
  ax = r[0] / 16384.0; ay = r[1] / 16384.0; az = r[2] / 16384.0;
  gx = r[4] / 131.0 - gbx; gy = r[5] / 131.0 - gby; gz = r[6] / 131.0 - gbz;
  return true;
}
void calibrateGyro() {                                     // keep the band still for ~1 s
  float sx = 0, sy = 0, sz = 0, ax, ay, az, gx, gy, gz;
  int n = 0;
  gbx = gby = gbz = 0;
  for (int i = 0; i < 200; i++) {
    if (mpuRead(ax, ay, az, gx, gy, gz)) { sx += gx; sy += gy; sz += gz; n++; }
    delay(5);
  }
  if (n > 0) { gbx = sx / n; gby = sy / n; gbz = sz / n; imuOk = true; }
}
void madgwickUpdate(float ax, float ay, float az, float gx, float gy, float gz, float dt) {
  const float D2R = PI / 180.0f;
  gx *= D2R; gy *= D2R; gz *= D2R;
  float qd0 = 0.5f * (-q1 * gx - q2 * gy - q3 * gz);
  float qd1 = 0.5f * ( q0 * gx + q2 * gz - q3 * gy);
  float qd2 = 0.5f * ( q0 * gy - q1 * gz + q3 * gx);
  float qd3 = 0.5f * ( q0 * gz + q1 * gy - q2 * gx);
  float n = sqrtf(ax * ax + ay * ay + az * az);
  if (n > 1e-6f) {
    ax /= n; ay /= n; az /= n;
    float f1 = 2 * (q1 * q3 - q0 * q2) - ax;
    float f2 = 2 * (q0 * q1 + q2 * q3) - ay;
    float f3 = 2 * (0.5f - q1 * q1 - q2 * q2) - az;
    float s0 = -2 * q2 * f1 + 2 * q1 * f2;
    float s1 =  2 * q3 * f1 + 2 * q0 * f2 - 4 * q1 * f3;
    float s2 = -2 * q0 * f1 + 2 * q3 * f2 - 4 * q2 * f3;
    float s3 =  2 * q1 * f1 + 2 * q2 * f2;
    float sn = sqrtf(s0 * s0 + s1 * s1 + s2 * s2 + s3 * s3);
    if (sn > 0) {
      qd0 -= MADGWICK_BETA * s0 / sn; qd1 -= MADGWICK_BETA * s1 / sn;
      qd2 -= MADGWICK_BETA * s2 / sn; qd3 -= MADGWICK_BETA * s3 / sn;
    }
  }
  q0 += qd0 * dt; q1 += qd1 * dt; q2 += qd2 * dt; q3 += qd3 * dt;
  n = sqrtf(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
  q0 /= n; q1 /= n; q2 /= n; q3 /= n;
}
int batteryPercent() {                                     // LiPo 3.3 V = 0 %, 4.2 V = 100 % (100k/100k divider)
  long mv = 0;
  for (int i = 0; i < 8; i++) mv += analogReadMilliVolts(BATT_PIN);
  float vbat = (mv / 8.0f) * 2.0f / 1000.0f;
  int pct = (int)((vbat - 3.3f) / (4.2f - 3.3f) * 100.0f);
  return pct < 0 ? 0 : (pct > 100 ? 100 : pct);
}

// ---- Wi-Fi + upload ----------------------------------------------------------------------------------------
void wifiConnect(unsigned long waitMs) {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                                    // lower latency for the live dashboard
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < waitMs) delay(250);
  Serial.println(WiFi.status() == WL_CONNECTED ? String("WiFi OK ") + WiFi.localIP().toString() : "WiFi not connected yet");
}

void handleCommand(const String &c) {
  if (c.startsWith("RATE,")) {
    int r = c.substring(5).toInt();
    if (r == 10 || r == 25 || r == 50) qRateHz = r;
  } else if (c.startsWith("VIB,")) {
    long ms = c.substring(4).toInt();
    if (ms > 0 && ms <= 2000) { vibUntil = millis() + ms; vibOn = true; }
  } else if (c == "CAL") {
    calRequested = true;
  }
}

void flushToPi() {
  if (outLen == 0 || WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  String url = String("http://") + PI_HOST + ":" + PI_PORT + "/api/band/ingest";
  if (!http.begin(url)) return;
  http.setTimeout(1500);
  http.addHeader("Content-Type", "text/plain");
  http.addHeader("Accept", "text/plain");
  http.addHeader("X-Band-Token", BAND_TOKEN);
  http.addHeader("X-Band-Id", DEVICE_ID);
  int code = http.POST((uint8_t *)outBuf, outLen);
  if (code == 200) {
    outLen = 0;                                            // delivered
    String body = http.getString();                        // "OK <accepted> <rejected>\n<command>\n..."
    int nl = body.indexOf('\n');
    while (nl >= 0 && nl + 1 < (int)body.length()) {
      int next = body.indexOf('\n', nl + 1);
      String line = body.substring(nl + 1, next < 0 ? body.length() : next);
      line.trim();
      if (line.length()) handleCommand(line);
      nl = next;
    }
  } else if (code == 401 || code == 403) {
    Serial.printf("Pi refused the token (HTTP %d): check BAND_TOKEN / SENSEBAND_BAND_TOKEN\n", code);
    outLen = 0;                                            // do not pile up data the Pi will never accept
  } else {
    Serial.printf("upload failed (HTTP %d), will retry\n", code);   // keep the buffer, retry next flush
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN);
  pinMode(VIB_PIN, OUTPUT);
  digitalWrite(VIB_PIN, LOW);
  analogReadResolution(12);

  mpuWrite(0x6B, 0x00); mpuWrite(0x1A, 0x03); mpuWrite(0x1B, 0x00); mpuWrite(0x1C, 0x00);
  delay(100);
  calibrateGyro();
  Serial.println(imuOk ? "MPU6050 OK" : "MPU6050 NOT FOUND - check wiring");

  ppgOk = ppg.begin(Wire, I2C_SPEED_FAST);
  if (ppgOk) {
    ppg.setup(60, 4, 2, 100, 411, 4096);                  // 25 samples/s into the FIFO (what the SpO2 algorithm expects)
    Serial.println("MAX30102 OK");
  } else {
    Serial.println("MAX30102 NOT FOUND - check wiring");
  }
  wifiConnect(15000);
  lastImu = millis();
}

void loop() {
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED && now - lastWifiTry > 5000) {   // reconnect forever
    lastWifiTry = now;
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
  if (calRequested) { calRequested = false; calibrateGyro(); }
  if (vibOn && (long)(now - vibUntil) >= 0) vibOn = false;
  digitalWrite(VIB_PIN, vibOn ? HIGH : LOW);

  // ---- IMU at 50 Hz ----
  if (imuOk && now - lastImu >= 20) {
    float dt = (now - lastImu) / 1000.0f;
    if (dt > 0.1f) dt = 0.1f;                              // an upload may have blocked us for a moment
    lastImu = now;
    float ax, ay, az, gx, gy, gz;
    if (mpuRead(ax, ay, az, gx, gy, gz)) {
      madgwickUpdate(ax, ay, az, gx, gy, gz, dt);
#if SEND_RAW_IMU
      char a[96];
      snprintf(a, sizeof a, "A,%lu,%.3f,%.3f,%.3f,%.1f,%.1f,%.1f", now, ax, ay, az, gx, gy, gz);
      emit(a);
#endif
    }
  }
  if (imuOk && now - lastQ >= (unsigned long)(1000 / qRateHz)) {
    lastQ = now;
    char m[64];
    snprintf(m, sizeof m, "Q,%lu,%.4f,%.4f,%.4f,%.4f", now, q0, q1, q2, q3);
    emit(m);
  }
  if (now - lastBatt >= 10000) {
    lastBatt = now;
    char m[16];
    snprintf(m, sizeof m, "B,%d", batteryPercent());
    emit(m);
  }

  // ---- PPG: read the FIFO without blocking ----
  if (ppgOk) {
    ppg.check();
    while (ppg.available()) {
      redBuf[bufIdx] = ppg.getFIFORed();
      irBuf[bufIdx] = ppg.getFIFOIR();
      ppg.nextSample();
      finger = irBuf[bufIdx] > 50000;
      if (++bufIdx == BUF) {
        maxim_heart_rate_and_oxygen_saturation(irBuf, BUF, redBuf, &spo2, &spo2Valid, &hr, &hrValid);
        uint32_t irMin = irBuf[0], irMax = irBuf[0];
        uint64_t irSum = 0;
        for (int i = 0; i < BUF; i++) {
          if (irBuf[i] < irMin) irMin = irBuf[i];
          if (irBuf[i] > irMax) irMax = irBuf[i];
          irSum += irBuf[i];
        }
        float irMean = irSum / (float)BUF;
        perfusion = irMean > 0 ? 100.0f * (irMax - irMin) / irMean : 0;
        char m[64];
        snprintf(m, sizeof m, "V,%ld,%d,%ld,%d,%d,%.1f", (long)hr, hrValid, (long)spo2, spo2Valid, finger ? 1 : 0, perfusion);
        emit(m);
        memmove(irBuf, irBuf + STEP, (BUF - STEP) * sizeof(uint32_t));
        memmove(redBuf, redBuf + STEP, (BUF - STEP) * sizeof(uint32_t));
        bufIdx = BUF - STEP;
      }
    }
  }

  if (now - lastFlush >= FLUSH_MS || outLen > OUT_CAP - 400) {
    lastFlush = now;
    flushToPi();
  }
}

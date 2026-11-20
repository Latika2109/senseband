/*
 * SenseBand – SMALL (supporting) band
 * ESP32 + MAX30102 (SpO2 / heart rate) + MPU6050 (IMU)
 * Sends data to the Raspberry Pi main band over Bluetooth Low Energy (BLE).
 *
 * Messages sent (plain text, easy to debug in Serial Monitor) - protocol v2, docs/05:
 *   Q,<millis>,<w>,<x>,<y>,<z>                orientation quaternion (Madgwick), 10 Hz
 *                                             (25 / 50 Hz after a RATE command)
 *   V,<hr>,<hrValid>,<spo2>,<spo2Valid>,<finger>,<perfusion>   vitals, once per second
 *   B,<battery_pct>                           battery, every 10 s
 * Commands (write text to the command characteristic ...b003):
 *   RATE,10|25|50     quaternion rate (50 Hz during exercises, 10 Hz otherwise to save power)
 *   VIB,<ms>          vibration-motor pulse (haptic tempo cue)
 *   CAL               gyro re-calibration (keep the band still for ~1 s)
 *
 * Libraries (Arduino IDE > Library Manager):
 *   - "SparkFun MAX3010x Pulse and Proximity Sensor Library"
 *   BLE comes with the ESP32 board package (Boards Manager > "esp32" by Espressif).
 *
 * Wiring (ESP32 D1 Mini). For ESP32-C3 SuperMini use SDA_PIN 8, SCL_PIN 9.
 *   MAX30102: VIN-3V3, GND-GND, SDA-21, SCL-22
 *   MPU6050 : VCC-3V3, GND-GND, SDA-21, SCL-22, AD0-GND
 *   Battery : LiPo+ -> 100k -> GPIO34 -> 100k -> GND (divider, reads half the battery voltage)
 *   Vibration motor (optional): GPIO25 -> 1k -> NPN base, motor between 3V3/VIN and collector
 */
#include <Wire.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

#define SDA_PIN 21
#define SCL_PIN 22
#define MPU_ADDR 0x68
#define DEBUG_SERIAL 1   // set 0 to stop printing every message to Serial

#define DEVICE_NAME  "SenseBand-S"
#define SERVICE_UUID "5e75e000-0000-4000-8000-00000000b001"
#define DATA_UUID    "5e75e000-0000-4000-8000-00000000b002"
#define CMD_UUID     "5e75e000-0000-4000-8000-00000000b003"
#define BATT_PIN     34      // ADC1 channel, 100k/100k divider
#define VIB_PIN      25      // optional haptic motor (via NPN)
#define MADGWICK_BETA 0.1f

MAX30105 ppg;
bool ppgOk = false;
BLECharacteristic *dataChar = nullptr;
BLECharacteristic *cmdChar = nullptr;
volatile bool bleConnected = false;

// ---- PPG: 100 samples @ 25 Hz = 4 s window, recalculated every 1 s ----
const int BUF = 100;
const int STEP = 25;
uint32_t irBuf[BUF], redBuf[BUF];
int bufIdx = 0;
int32_t spo2 = 0, hr = 0;
int8_t spo2Valid = 0, hrValid = 0;
bool finger = false;

// ---- IMU ----
float gbx = 0, gby = 0, gbz = 0;      // gyro bias
float q0 = 1, q1 = 0, q2 = 0, q3 = 0; // orientation quaternion (w, x, y, z)
bool imuOk = false;
unsigned long lastImu = 0, lastQSend = 0, lastBatt = 0;
volatile int qRateHz = 10;            // set by the RATE command
volatile bool calRequested = false;   // set by the CAL command
volatile unsigned long vibUntil = 0;  // set by the VIB command
volatile bool vibOn = false;
float perfusion = 0;                  // IR AC/DC in %, from the same 4 s window as SpO2

class ServerCB : public BLEServerCallbacks {
  void onConnect(BLEServer *s) override { bleConnected = true; }
  void onDisconnect(BLEServer *s) override {
    bleConnected = false;
    BLEDevice::startAdvertising();      // let the Pi reconnect
  }
};

// Commands from the Pi. Only flags are set here (BLE task); the work happens in loop().
class CmdCB : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    auto v = c->getValue();             // std::string (core 2.x) or String (core 3.x)
    const char *t = v.c_str();
    if (strncmp(t, "RATE,", 5) == 0) {
      int r = atoi(t + 5);
      if (r == 10 || r == 25 || r == 50) qRateHz = r;
    } else if (strncmp(t, "VIB,", 4) == 0) {
      long ms = atol(t + 4);
      if (ms > 0 && ms <= 2000) { vibUntil = millis() + ms; vibOn = true; }
    } else if (strcmp(t, "CAL") == 0) {
      calRequested = true;
    }
  }
};

void send(const char *msg) {
#if DEBUG_SERIAL
  Serial.println(msg);
#endif
  if (bleConnected && dataChar) {
    dataChar->setValue((uint8_t *)msg, strlen(msg));
    dataChar->notify();
  }
}

// ---------------- MPU6050 ----------------
void mpuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

bool mpuRead(float &ax, float &ay, float &az, float &gx, float &gy, float &gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14) != 14) return false;
  int16_t r[7];
  for (int i = 0; i < 7; i++) {
    uint8_t hi = Wire.read();
    uint8_t lo = Wire.read();
    r[i] = (int16_t)((hi << 8) | lo);
  }
  ax = r[0] / 16384.0; ay = r[1] / 16384.0; az = r[2] / 16384.0;   // g  (±2 g range)
  gx = r[4] / 131.0 - gbx; gy = r[5] / 131.0 - gby; gz = r[6] / 131.0 - gbz;  // deg/s (±250)
  return true;
}

void calibrateGyro() {
  // Keep the band STILL for ~1 s after power-on
  float sx = 0, sy = 0, sz = 0, ax, ay, az, gx, gy, gz;
  int n = 0;
  gbx = gby = gbz = 0;
  for (int i = 0; i < 200; i++) {
    if (mpuRead(ax, ay, az, gx, gy, gz)) { sx += gx; sy += gy; sz += gz; n++; }
    delay(5);
  }
  if (n > 0) { gbx = sx / n; gby = sy / n; gbz = sz / n; imuOk = true; }
}

// ---------------- Madgwick IMU filter (same algorithm as edge/senseband_edge/pipeline/madgwick.py)
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

// Battery % from the 100k/100k divider: LiPo 3.3 V = 0 %, 4.2 V = 100 %
int batteryPercent() {
  long mv = 0;
  for (int i = 0; i < 8; i++) mv += analogReadMilliVolts(BATT_PIN);
  float vbat = (mv / 8.0f) * 2.0f / 1000.0f;
  int pct = (int)((vbat - 3.3f) / (4.2f - 3.3f) * 100.0f);
  return pct < 0 ? 0 : (pct > 100 ? 100 : pct);
}

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN);
  pinMode(VIB_PIN, OUTPUT);
  digitalWrite(VIB_PIN, LOW);
  analogReadResolution(12);

  // IMU
  mpuWrite(0x6B, 0x00);   // wake up
  mpuWrite(0x1A, 0x03);   // low-pass filter ~44 Hz
  mpuWrite(0x1B, 0x00);   // gyro ±250 deg/s
  mpuWrite(0x1C, 0x00);   // accel ±2 g
  delay(100);
  calibrateGyro();
  Serial.println(imuOk ? "MPU6050 OK" : "MPU6050 NOT FOUND - check wiring");

  // MAX30102
  ppgOk = ppg.begin(Wire, I2C_SPEED_FAST);
  if (ppgOk) {
    // brightness 60, average 4, red+IR mode, 100 Hz, pulse 411 us, range 4096
    // => 25 samples/s into the FIFO, which is what the SpO2 algorithm expects
    ppg.setup(60, 4, 2, 100, 411, 4096);
    Serial.println("MAX30102 OK");
  } else {
    Serial.println("MAX30102 NOT FOUND - check wiring");
  }

  // BLE
  BLEDevice::init(DEVICE_NAME);
  BLEDevice::setMTU(185);
  BLEServer *server = BLEDevice::createServer();
  server->setCallbacks(new ServerCB());
  BLEService *svc = server->createService(SERVICE_UUID);
  dataChar = svc->createCharacteristic(
      DATA_UUID, BLECharacteristic::PROPERTY_NOTIFY | BLECharacteristic::PROPERTY_READ);
  dataChar->addDescriptor(new BLE2902());
  cmdChar = svc->createCharacteristic(
      CMD_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  cmdChar->setCallbacks(new CmdCB());
  svc->start();
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  Serial.println("BLE advertising as " DEVICE_NAME);

  lastImu = millis();
}

void loop() {
  unsigned long now = millis();

  // ---- commands handled here (not in the BLE callback) ----
  if (calRequested) { calRequested = false; calibrateGyro(); }
  if (vibOn && (long)(now - vibUntil) >= 0) vibOn = false;          // millis() wrap-safe
  digitalWrite(VIB_PIN, vibOn ? HIGH : LOW);

  // ---- IMU at 50 Hz: Madgwick filter -> quaternion ----
  if (imuOk && now - lastImu >= 20) {
    float dt = (now - lastImu) / 1000.0f;
    lastImu = now;
    float ax, ay, az, gx, gy, gz;
    if (mpuRead(ax, ay, az, gx, gy, gz)) madgwickUpdate(ax, ay, az, gx, gy, gz, dt);
  }
  // quaternion out at qRateHz (10 Hz normally, 50 Hz during exercises)
  if (imuOk && now - lastQSend >= (unsigned long)(1000 / qRateHz)) {
    lastQSend = now;
    char m[64];
    snprintf(m, sizeof m, "Q,%lu,%.4f,%.4f,%.4f,%.4f", now, q0, q1, q2, q3);
    send(m);
  }

  // ---- battery every 10 s ----
  if (now - lastBatt >= 10000) {
    lastBatt = now;
    char m[16];
    snprintf(m, sizeof m, "B,%d", batteryPercent());
    send(m);
  }

  // ---- PPG: read FIFO without blocking ----
  if (ppgOk) {
    ppg.check();
    while (ppg.available()) {
      redBuf[bufIdx] = ppg.getFIFORed();
      irBuf[bufIdx]  = ppg.getFIFOIR();
      ppg.nextSample();
      finger = irBuf[bufIdx] > 50000;           // finger/earlobe present
      if (++bufIdx == BUF) {
        maxim_heart_rate_and_oxygen_saturation(irBuf, BUF, redBuf,
                                               &spo2, &spo2Valid, &hr, &hrValid);
        // perfusion index: IR pulsatile part relative to its DC level (percent)
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
        snprintf(m, sizeof m, "V,%ld,%d,%ld,%d,%d,%.1f",
                 (long)hr, hrValid, (long)spo2, spo2Valid, finger ? 1 : 0, perfusion);
        send(m);
        // keep last 75 samples, collect 25 new ones => new result every 1 s
        memmove(irBuf,  irBuf + STEP,  (BUF - STEP) * sizeof(uint32_t));
        memmove(redBuf, redBuf + STEP, (BUF - STEP) * sizeof(uint32_t));
        bufIdx = BUF - STEP;
      }
    }
  }
}

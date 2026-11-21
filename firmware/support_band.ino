// Support band

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include "MAX30105.h"

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASS";
const char* PI_IP     = "192.168.1.50";
const int   PI_PORT   = 5005;

WiFiUDP udp;
MAX30105 ppg;

float imu[5][6];  int nImu = 0;
unsigned long redV[5], irV[5]; int nPpg = 0;
unsigned long tImu, tSend;

void mpuRead(float* o) {
  Wire.beginTransmission(0x68); Wire.write(0x3B); Wire.endTransmission(false);
  Wire.requestFrom(0x68, 14, true);
  int16_t v[7];
  for (int i = 0; i < 7; i++) { int16_t h = Wire.read(); v[i] = (h << 8) | Wire.read(); }
  o[0] = v[0] / 16384.0; o[1] = v[1] / 16384.0; o[2] = v[2] / 16384.0;
  o[3] = v[4] / 131.0;   o[4] = v[5] / 131.0;   o[5] = v[6] / 131.0;
}

void pushPpg(unsigned long r, unsigned long ir) {
  if (nPpg == 5) {
    for (int i = 1; i < 5; i++) { redV[i - 1] = redV[i]; irV[i - 1] = irV[i]; }
    nPpg = 4;
  }
  redV[nPpg] = r; irV[nPpg] = ir; nPpg++;
}

void sendPacket() {
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); nImu = nPpg = 0; return; }
  char buf[700];
  int n = snprintf(buf, sizeof(buf), "{\"d\":\"sup\",\"imu\":[");
  for (int i = 0; i < nImu; i++)
    n += snprintf(buf + n, sizeof(buf) - n, "%s[%.3f,%.3f,%.3f,%.1f,%.1f,%.1f]", i ? "," : "",
                  imu[i][0], imu[i][1], imu[i][2], imu[i][3], imu[i][4], imu[i][5]);
  n += snprintf(buf + n, sizeof(buf) - n, "],\"red\":[");
  for (int i = 0; i < nPpg; i++) n += snprintf(buf + n, sizeof(buf) - n, "%s%lu", i ? "," : "", redV[i]);
  n += snprintf(buf + n, sizeof(buf) - n, "],\"ir\":[");
  for (int i = 0; i < nPpg; i++) n += snprintf(buf + n, sizeof(buf) - n, "%s%lu", i ? "," : "", irV[i]);
  n += snprintf(buf + n, sizeof(buf) - n, "]}");
  udp.beginPacket(PI_IP, PI_PORT);
  udp.write((uint8_t*)buf, n);
  udp.endPacket();
  nImu = nPpg = 0;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000);
  Wire.beginTransmission(0x68); Wire.write(0x6B); Wire.write(0); Wire.endTransmission();
  if (!ppg.begin(Wire, I2C_SPEED_FAST)) Serial.println("MAX30102 not found");
  ppg.setup(0x3C, 1, 2, 50, 411, 4096);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(300);
  Serial.println(WiFi.localIP());
  tImu = tSend = millis();
}

void loop() {
  unsigned long now = millis();
  if (now - tImu >= 20) { tImu += 20; if (nImu < 5) mpuRead(imu[nImu++]); }
  ppg.check();
  while (ppg.available()) { pushPpg(ppg.getFIFORed(), ppg.getFIFOIR()); ppg.nextSample(); }
  if (now - tSend >= 100) { tSend += 100; sendPacket(); }
}

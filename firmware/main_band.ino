// Main band

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASS";
const char* PI_IP     = "192.168.1.50";
const int   PI_PORT   = 5005;

#define ECG_PIN  34
#define TEMP_PIN 4
#define SOS_PIN  13

WiFiUDP udp;
OneWire oneWire(TEMP_PIN);
DallasTemperature ds(&oneWire);

float imu[5][6];  int nImu = 0;
int   ecg[12];    int nEcg = 0;
float tempC = 36.5;
unsigned long tImu, tEcg, tTemp, tSend;

void mpuRead(float* o) {
  Wire.beginTransmission(0x68); Wire.write(0x3B); Wire.endTransmission(false);
  Wire.requestFrom(0x68, 14, true);
  int16_t v[7];
  for (int i = 0; i < 7; i++) { int16_t h = Wire.read(); v[i] = (h << 8) | Wire.read(); }
  o[0] = v[0] / 16384.0; o[1] = v[1] / 16384.0; o[2] = v[2] / 16384.0;
  o[3] = v[4] / 131.0;   o[4] = v[5] / 131.0;   o[5] = v[6] / 131.0;
}

void sendPacket() {
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); nImu = nEcg = 0; return; }
  char buf[700];
  int n = snprintf(buf, sizeof(buf), "{\"d\":\"main\",\"imu\":[");
  for (int i = 0; i < nImu; i++)
    n += snprintf(buf + n, sizeof(buf) - n, "%s[%.3f,%.3f,%.3f,%.1f,%.1f,%.1f]", i ? "," : "",
                  imu[i][0], imu[i][1], imu[i][2], imu[i][3], imu[i][4], imu[i][5]);
  n += snprintf(buf + n, sizeof(buf) - n, "],\"ecg\":[");
  for (int i = 0; i < nEcg; i++) n += snprintf(buf + n, sizeof(buf) - n, "%s%d", i ? "," : "", ecg[i]);
  n += snprintf(buf + n, sizeof(buf) - n, "],\"temp\":%.1f,\"sos\":%d}", tempC, digitalRead(SOS_PIN) == LOW ? 1 : 0);
  udp.beginPacket(PI_IP, PI_PORT);
  udp.write((uint8_t*)buf, n);
  udp.endPacket();
  nImu = nEcg = 0;
}

void setup() {
  Serial.begin(115200);
  pinMode(SOS_PIN, INPUT_PULLUP);
  Wire.begin();
  Wire.setClock(400000);
  Wire.beginTransmission(0x68); Wire.write(0x6B); Wire.write(0); Wire.endTransmission();
  ds.begin();
  ds.setWaitForConversion(false);
  ds.requestTemperatures();
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(300);
  Serial.println(WiFi.localIP());
  tImu = tEcg = tTemp = tSend = millis();
}

void loop() {
  unsigned long now = millis();
  if (now - tImu >= 20)    { tImu += 20;    if (nImu < 5)  mpuRead(imu[nImu++]); }
  if (now - tEcg >= 8)     { tEcg += 8;     if (nEcg < 12) ecg[nEcg++] = analogRead(ECG_PIN); }
  if (now - tTemp >= 1000) {
    tTemp += 1000;
    float t = ds.getTempCByIndex(0);
    if (t > -100) tempC = t;
    ds.requestTemperatures();
  }
  if (now - tSend >= 100)  { tSend += 100;  sendPacket(); }
}

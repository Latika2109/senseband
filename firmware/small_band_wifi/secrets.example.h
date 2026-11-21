// Copy this file to secrets.h (same folder) and fill it in. secrets.h is git-ignored: never commit it.
#pragma once
#define WIFI_SSID   "YOUR_WIFI_NAME"
#define WIFI_PASS   "YOUR_WIFI_PASSWORD"
#define PI_HOST     "10.59.27.40"     // the Raspberry Pi's IP address
#define PI_PORT     8080
#define DEVICE_ID   "S1"              // S1 = primary small band (SpO2, pulse, joint quaternion). Use S2 on the second ESP32.
#define BAND_TOKEN  "PASTE_THE_SENSEBAND_BAND_TOKEN_HERE"   // same value as SENSEBAND_BAND_TOKEN on the Pi
#define SEND_RAW_IMU 1                // 1 = also send raw accel/gyro (A lines) so this ESP32 can act as the WAIST IMU

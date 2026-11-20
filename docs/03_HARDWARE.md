# 03 – Hardware (source of truth for pins)

## Main band – Raspberry Pi 3 (physical pin numbers)
| Module | Module pin | Pi pin | Notes |
|---|---|---|---|
| I2C bus | 3.3V / GND / SDA / SCL | 1 / 6 / 3 (GPIO2) / 5 (GPIO3) | shared by MPU6050, ADS1115, (MLX90614) |
| MPU6050 | VCC GND SDA SCL; AD0→GND | I2C bus | addr 0x68 |
| ADS1115 | VDD GND SDA SCL; ADDR→GND | I2C bus | addr 0x48; A0 = ECG, A1 = grip |
| AD8232 ECG | 3.3V / GND | 17 / 9 | |
| | OUTPUT | ADS1115 A0 | |
| | LO+ / LO− | 11 (GPIO17) / 13 (GPIO27) | lead-off detect |
| | SDN | 15 (GPIO22) | high = ECG on |
| Grip FSR402 | FSR: 3.3V → A1; 10 kΩ: A1 → GND | ADS1115 A1 | piezo only gives relative values |
| DS18B20 | VDD / GND / DATA (+4.7 kΩ DATA→3.3V) | 1 / 14 / 7 (GPIO4) | or MLX90614 on I2C (0x5A) |
| SIM800L | VCC / GND | own 18650 (3.7–4.2 V) + 1000 µF cap; GND common with Pi | never from Pi pins |
| | TXD → Pi RX | 10 (GPIO15) | direct |
| | RXD ← Pi TX | 8 (GPIO14) | via 10 kΩ / 20 kΩ divider |
| **NEW** "I'm OK" / SOS button | one side → GPIO, other → GND | 16 (GPIO23) | internal pull-up; long-press = SOS |
| **NEW** Buzzer (active, 3.3 V) | + / − | 18 (GPIO24) / 20 | via NPN (2N2222 + 1 kΩ) if it draws > 15 mA |
| **NEW** Status LED | anode via 330 Ω / cathode | 22 (GPIO25) / 25 | |

ECG electrode placement (waist band, leads on extendable cable): **RA** below right collarbone,
**LA** below left collarbone, **RL** lower left abdomen. ⚠️ ECG only when running on battery, never
while plugged into a mains charger.

## Small band – ESP32 (D1 Mini pins; C3 SuperMini: SDA 8, SCL 9)
| Module | Pin | ESP32 |
|---|---|---|
| MAX30102 | VIN / GND / SDA / SCL / INT | 3V3 / GND / 21 / 22 / 4 (optional) |
| MPU6050 | VCC / GND / SDA / SCL, AD0→GND | 3V3 / GND / 21 / 22 |
| Battery | LiPo 3.7 V → TP4056 (protected) → 5V/VIN | battery % via 100 k/100 k divider → GPIO34 (ADC) |
| Vibration motor (optional) | via NPN | GPIO 25 – haptic tempo cue during exercises |

SpO2 clip: 3D-printed black finger/ear clip, sensor window on the finger pad, foam ring to block
light, gentle spring/elastic pressure, 4-wire flexible cable to the band.

## How to wear for recovery (main band above joint, small band below)
| Joint | Main band (upper / reference) | Small band (lower / moving) | Movements measured |
|---|---|---|---|
| Elbow | upper arm, middle, outer side | forearm, middle, back side | flexion/extension, pronation/supination (roll) |
| Wrist | forearm, near wrist | back of the hand | flexion/extension, radial/ulnar deviation, + grip |
| Shoulder | waist (trunk reference) | upper arm, middle | flexion, abduction, rotation |
| Hip | waist | thigh, middle, front | flexion, abduction |
| Knee | thigh, lower third, front | shin, upper third, front | flexion/extension |
| Ankle | shin, lower third | top of the foot | dorsiflexion/plantarflexion |
Both bands use elastic Velcro straps; the app shows a picture for each joint.

## Bill of materials (approx. INR, Nov 2026 – verify locally)
| Item | Qty | ~₹ |
|---|---|---|
| Raspberry Pi 3 (have) / future: **Pi Zero 2 W** (smaller, ~1 W, same software) | 1 | (have) / 1,800 |
| ESP32 D1 Mini or C3 SuperMini | 1 | 350 |
| MPU6050 | 2 | 2 × 150 |
| MAX30102 | 1 | 250 |
| AD8232 + electrodes pack | 1 | 600 |
| ADS1115 | 1 | 250 |
| DS18B20 (or MLX90614) | 1 | 100 (900) |
| FSR402 (or load cell + HX711) | 1 | 350 (400) |
| SIM800L + antenna | 1 | 450 |
| 18650 cells + holders, 5 V 3 A boost, TP4056 ×2, LiPo 1000 mAh | – | 1,200 |
| Button, active buzzer, LED, resistors, caps, 2N2222, wires, Velcro straps | – | 300 |
| 3D printing (enclosures, SpO2 clip, grip plier) | – | 500 |

## Power budget (24 h target)
| Band | Average draw | Battery |
|---|---|---|
| Pi 3 + sensors (HDMI off, Lite OS) | ~2.2 W ≈ 440 mA @ 5 V → ~10.5 Ah @5 V/24 h | 3 × 18650 (≈ 10 Ah @3.7 V ≈ 7.5 Ah @5 V after boost) → **~17–20 h**; 24 h needs 4 cells or **Pi Zero 2 W (~1 W → 24 h on 2 cells)** |
| SIM800L idle/sleep | ~1–10 mA, 2 A peaks | 1 × 18650 |
| ESP32 + BLE + MAX30102 | ~70 mA | 1500–2000 mAh LiPo → ~24 h |
Software savings: ECG SDN off during sleep if HR from PPG is stable, BLE notify at 10 Hz in monitoring
mode (25–50 Hz only during exercises), CPU governor `ondemand`.

## Bring-up checklist
1. `i2cdetect -y 1` → 48, 68 (+5a). 2. `ls /sys/bus/w1/devices` → 28-… 3. SIM: `AT` → OK, `AT+CSQ` signal.
4. ESP32 Serial Monitor shows `I,…` and `V,…` lines. 5. `bluetoothctl scan on` shows SenseBand-S.
6. Button press logs on GPIO23, buzzer beeps on GPIO24.

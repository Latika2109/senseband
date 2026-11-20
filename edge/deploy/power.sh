#!/bin/sh
# Battery savings applied before the service starts (docs/03 "Software savings"). Every step is best-effort:
# a missing tool or a read-only file must never stop monitoring from starting.
set -u
# CPU governor: ondemand (falls back silently to whatever the kernel offers)
for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  [ -w "$g" ] && echo ondemand > "$g" 2>/dev/null
done
# HDMI off (about 25 mA) and the activity LED off
command -v tvservice >/dev/null 2>&1 && tvservice -o >/dev/null 2>&1
command -v vcgencmd  >/dev/null 2>&1 && vcgencmd display_power 0 >/dev/null 2>&1
[ -w /sys/class/leds/ACT/brightness ] && echo 0 > /sys/class/leds/ACT/brightness 2>/dev/null
# Wi-Fi power save on (the app talks to the band over the local network, latency of a few ms is fine)
command -v iw >/dev/null 2>&1 && iw dev wlan0 set power_save on >/dev/null 2>&1
exit 0

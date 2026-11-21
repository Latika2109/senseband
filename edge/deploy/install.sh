#!/bin/sh
# Install SenseBand on a Raspberry Pi OS Lite (run as root, from the repository root).  Idempotent.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo sh edge/deploy/install.sh"; exit 1; }
ROOT=/opt/senseband
VER=$(cat VERSION 2>/dev/null || echo 0.1.0)
id senseband >/dev/null 2>&1 || useradd --system --home /var/lib/senseband --create-home --shell /usr/sbin/nologin senseband
for g in i2c gpio dialout bluetooth; do getent group $g >/dev/null && usermod -aG $g senseband || true; done
mkdir -p "$ROOT/releases/$VER" /var/lib/senseband /etc/senseband
cp -r edge ml VERSION "$ROOT/releases/$VER"/
mkdir -p "$ROOT/releases/$VER/app"
[ -d app/dist ] && cp -r app/dist "$ROOT/releases/$VER/app/" || echo "warning: app/dist missing - the band will run without the phone UI (build it: cd app && npm i && npm run build)"
ln -sfn "$ROOT/releases/$VER" "$ROOT/current"
[ -d "$ROOT/venv" ] || python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install --quiet numpy scipy pyyaml pydantic fastapi uvicorn reportlab cryptography onnxruntime \
    smbus2 gpiozero pyserial bleak
[ -f /etc/senseband/config.yaml ] || cp edge/deploy/config.example.yaml /etc/senseband/config.yaml
if [ ! -f /etc/senseband/senseband.env ]; then      # secrets file: created once, never overwritten, root/senseband only
  TOK=$(python3 -c "import secrets;print(secrets.token_hex(24))")
  sed "s/^SENSEBAND_BAND_TOKEN=.*/SENSEBAND_BAND_TOKEN=$TOK/" .env.example > /etc/senseband/senseband.env
  chown root:senseband /etc/senseband/senseband.env && chmod 640 /etc/senseband/senseband.env
  echo "Created /etc/senseband/senseband.env: add your Twilio values there. Band token for the ESP32s: $TOK"
fi
chown -R senseband:senseband /var/lib/senseband "$ROOT"
cp edge/deploy/senseband.service edge/deploy/senseband-update.service edge/deploy/senseband-update.timer /etc/systemd/system/
cp edge/deploy/senseband.logrotate /etc/logrotate.d/senseband
chmod +x "$ROOT/current/edge/deploy/power.sh"
systemctl daemon-reload
systemctl enable --now senseband.service
systemctl enable --now senseband-update.timer
echo "SenseBand $VER installed. Edit /etc/senseband/config.yaml (contacts, phone numbers, cloud), then: systemctl restart senseband"

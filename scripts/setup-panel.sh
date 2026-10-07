#!/usr/bin/env bash
# Status LEDs and buttons on the Pi's GPIO header (see printserver/panel.py).
#
#   ./scripts/setup-panel.sh            install / update and start
#   ./scripts/setup-panel.sh --disable  stop it and turn the LEDs off
#
# Wiring (BCM pin, physical pin): each LED through a 220-330 ohm resistor to
# ground; each button between its pin and ground.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=/etc/systemd/system/print-panel.service

if [[ "${1:-}" == "--disable" ]]; then
  sudo systemctl disable --now print-panel 2>/dev/null || true
  sudo rm -f "$UNIT"
  sudo systemctl daemon-reload
  echo "Status panel switched off."
  exit 0
fi

echo "==> Checking for gpiozero (GPIO library)"
if ! /usr/bin/python3 -c "import gpiozero" 2>/dev/null; then
  if sudo apt-get install -y python3-gpiozero python3-lgpio; then
    :
  else
    echo "Could not install python3-gpiozero (it needs internet once)." >&2
    echo "Connect the Pi to the internet for a moment and run this again." >&2
    exit 1
  fi
fi

echo "==> Installing the print-panel service"
sed -e "s|__DIR__|$DIR|g" "$DIR/systemd/print-panel.service" | sudo tee "$UNIT" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable print-panel >/dev/null 2>&1
sudo systemctl restart print-panel
sleep 3
if ! systemctl is-active --quiet print-panel; then
  echo "The panel didn't start. See: journalctl -u print-panel -n 30" >&2
  exit 1
fi

cat <<'INFO'

Done! The status panel is running.

  LED / button        BCM pin  physical pin
  POWER LED           GPIO 5   29
  NETWORK LED         GPIO 6   31
  SERVER LED          GPIO 13  33
  PRINTER LED         GPIO 19  35
  FIX button          GPIO 26  37
  POWER button        GPIO 3   5   (its other side to ground, pin 6)
  ground for the rest          39

Different pins: set PANEL_LED_POWER=..., PANEL_BUTTON_FIX=... etc. in
/etc/default/print-server, then: sudo systemctl restart print-panel
INFO

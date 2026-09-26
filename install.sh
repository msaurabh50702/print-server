#!/usr/bin/env bash
# Install the print server on Raspberry Pi OS (Bookworm or newer).
# Usage:  ./install.sh            (installs everything incl. LibreOffice for Word/Excel files)
#         ./install.sh --no-office (skip LibreOffice; PDFs and images still work)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
WITH_OFFICE=1
[[ "${1:-}" == "--no-office" ]] && WITH_OFFICE=0

if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
  echo "Run this as your normal user (it uses sudo where needed)." >&2
  exit 1
fi

echo "==> Installing system packages"
PKGS=(cups cups-client poppler-utils python3-venv python3-pip avahi-daemon)
[[ $WITH_OFFICE -eq 1 ]] && PKGS+=(libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress)
sudo apt-get update
sudo apt-get install -y --no-install-recommends "${PKGS[@]}"

echo "==> Allowing $RUN_USER to manage printers"
sudo usermod -aG lpadmin "$RUN_USER"
sudo systemctl enable --now cups avahi-daemon

echo "==> Creating Python virtualenv"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

echo "==> Installing systemd service"
if [[ ! -f /etc/default/print-server ]]; then
  sudo install -m 644 "$DIR/systemd/print-server.env" /etc/default/print-server
fi
sed -e "s|__USER__|$RUN_USER|g" -e "s|__DIR__|$DIR|g" "$DIR/systemd/print-server.service" \
  | sudo tee /etc/systemd/system/print-server.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now print-server
sudo systemctl restart print-server

HOST="$(hostname)"
IP="$(hostname -I | awk '{print $1}')"
cat <<EOF

Done! Next steps:
  1. Install the Canon driver and add the printer:   ./scripts/setup-printer.sh
  2. Open on any phone/PC on the same Wi-Fi:  http://$HOST.local   (or http://$IP)

Logs:      journalctl -u print-server -f
Settings:  /etc/default/print-server  (then: sudo systemctl restart print-server)
EOF

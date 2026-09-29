#!/usr/bin/env bash
# Install the print server on 64-bit Raspberry Pi OS (Bookworm or newer).
# Usage:  ./install.sh               installs everything incl. LibreOffice for Word/Excel files
#         ./install.sh --no-office   skip LibreOffice (PDFs and images still work)
#         ./install.sh --skip-checks install even if the system checks fail
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
WITH_OFFICE=1
SKIP_CHECKS=0
for arg in "$@"; do
  case "$arg" in
    --no-office) WITH_OFFICE=0 ;;
    --skip-checks) SKIP_CHECKS=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
  echo "Run this as your normal user (it uses sudo where needed)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# System checks: catch an unusable setup before installing anything.
# ---------------------------------------------------------------------------
echo "==> Checking this system"
PROBLEMS=()

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "$ARCH" in
  arm64|amd64) ;;
  armhf|armel|i386)
    PROBLEMS+=("This is a 32-bit system ($ARCH). Canon's printer driver needs 64-bit.
     Reinstall with 'Raspberry Pi OS Lite (64-bit)' using Raspberry Pi Imager
     (needs a Pi 3, 4, 5 or Zero 2 W).") ;;
esac

OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
if [[ -r "$OS_RELEASE_FILE" ]]; then
  . "$OS_RELEASE_FILE"
  MAJOR="${VERSION_ID%%.*}"
  if [[ "${ID:-}" =~ ^(debian|raspbian)$ && -n "$MAJOR" && "$MAJOR" -lt 12 ]]; then
    PROBLEMS+=("${PRETTY_NAME:-This OS} is too old and no longer receives packages.
     Install Raspberry Pi OS (64-bit), Bookworm or newer.")
  elif [[ "${ID:-}" == "ubuntu" && -n "$MAJOR" && "$MAJOR" -lt 22 ]]; then
    PROBLEMS+=("${PRETTY_NAME:-This OS} is too old. Use Ubuntu 22.04 or newer.")
  fi
fi

if [[ "$(date +%Y)" -lt 2025 ]]; then
  PROBLEMS+=("The clock is wrong ($(date)). Package downloads will fail.
     Connect to the internet and run:  sudo timedatectl set-ntp true
     or set it by hand:                sudo date -s \"2026-01-31 10:00\"")
fi

APT_SOURCES="${APT_SOURCES:-/etc/apt/sources.list /etc/apt/sources.list.d/}"
# shellcheck disable=SC2086
if grep -rqsE '^[^#]*debian/? +(sid|unstable)' $APT_SOURCES; then
  echo "    Warning: a Debian 'sid' (unstable) repository is enabled. It can break" >&2
  echo "    Raspberry Pi OS; consider removing it from /etc/apt/sources.list*." >&2
fi

if command -v python3 >/dev/null && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  PROBLEMS+=("Python $(python3 -V 2>&1 | cut -d' ' -f2) is too old; 3.9 or newer is needed.")
fi

if [[ ${#PROBLEMS[@]} -gt 0 ]]; then
  echo >&2
  echo "This system can't run the print server yet:" >&2
  for p in "${PROBLEMS[@]}"; do echo "  - $p" >&2; done
  echo >&2
  if [[ $SKIP_CHECKS -eq 0 ]]; then
    echo "Fix the above, or re-run with --skip-checks to try anyway." >&2
    exit 1
  fi
  echo "Continuing because of --skip-checks." >&2
fi

echo "==> Installing system packages"
PKGS=(cups cups-client poppler-utils python3-venv python3-pip avahi-daemon)
[[ $WITH_OFFICE -eq 1 ]] && PKGS+=(libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress)
sudo apt-get update
sudo apt-get install -y --no-install-recommends "${PKGS[@]}"

echo "==> Allowing $RUN_USER to manage printers"
sudo usermod -aG lpadmin "$RUN_USER"
sudo systemctl enable --now cups avahi-daemon

echo "==> Allowing the web app to restart and shut down the Pi"
SUDOERS_TMP="$(mktemp)"
echo "$RUN_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff" > "$SUDOERS_TMP"
if sudo visudo -cf "$SUDOERS_TMP" >/dev/null; then
  sudo install -m 440 "$SUDOERS_TMP" /etc/sudoers.d/print-server
else
  echo "    (skipped: sudoers rule failed validation)"
fi
rm -f "$SUDOERS_TMP"

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

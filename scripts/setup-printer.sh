#!/usr/bin/env bash
# Add the USB-connected Canon MF4820d (or another USB printer) to CUPS.
#
# The MF4820d speaks Canon's UFR II LT language, so it needs Canon's
# "UFR II / UFRII LT Printer Driver for Linux". Download the ARM64 .deb from
# Canon's support site and pass it to this script, or install it yourself first:
#
#   ./scripts/setup-printer.sh ~/Downloads/cnrdrvcups-ufr2-uk_*_arm64.deb
#
# Optional env vars: QUEUE_NAME (default Canon_MF4820d), PPD (force a PPD/model).
set -euo pipefail

QUEUE_NAME="${QUEUE_NAME:-Canon_MF4820d}"

if [[ $# -ge 1 ]]; then
  echo "==> Installing Canon driver package $1"
  sudo apt-get install -y "$(realpath "$1")"
  sudo systemctl restart cups
fi

echo "==> Looking for a USB printer"
URI="$(sudo lpinfo -v 2>/dev/null | awk '/usb:\/\/Canon/ {print $2; exit}')"
if [[ -z "$URI" ]]; then
  URI="$(sudo lpinfo -v 2>/dev/null | awk '/usb:\/\// {print $2; exit}')"
fi
if [[ -z "$URI" ]]; then
  echo "No USB printer found. Check the cable, switch the printer on, then run 'sudo lpinfo -v'." >&2
  exit 1
fi
echo "    found $URI"

echo "==> Looking for a driver (PPD)"
MODEL="${PPD:-$(lpinfo -m 2>/dev/null | grep -iE 'MF4800|MF4820' | head -n1 | awk '{print $1}')}"
if [[ -z "$MODEL" ]]; then
  cat >&2 <<'EOF'
No Canon MF4800-series driver is installed.

 1. On Canon's support site, search "MF4820d" -> Drivers -> Linux and download
    "UFR II/UFRII LT Printer Driver for Linux" (V5.x or newer includes ARM64 builds).
 2. Extract it and copy the *arm64.deb (e.g. cnrdrvcups-ufr2-uk_*_arm64.deb) to the Pi.
 3. Re-run:  ./scripts/setup-printer.sh path/to/that.deb

Check the Pi is on 64-bit Raspberry Pi OS with:  dpkg --print-architecture   (should print arm64)
EOF
  exit 1
fi
echo "    using $MODEL"

echo "==> Creating CUPS queue '$QUEUE_NAME'"
sudo lpadmin -p "$QUEUE_NAME" -E -v "$URI" -m "$MODEL" \
  -o media=A4 -o PageSize=A4 -o printer-is-shared=true
sudo lpadmin -d "$QUEUE_NAME"
sudo cupsenable "$QUEUE_NAME"
sudo cupsaccept "$QUEUE_NAME"

# Also share the printer on the LAN so Windows/Mac/phones can add it directly.
sudo cupsctl --share-printers

echo "==> Done. Status:"
lpstat -p "$QUEUE_NAME"
echo
echo "Test page:  lp -d $QUEUE_NAME /usr/share/cups/data/testprint"

#!/usr/bin/env bash
# Add a printer to CUPS so the print server can use it. Run it once per printer.
#
#   Canon MF4820d (USB, default printer):
#     ./scripts/setup-printer.sh canon ~/cnrdrvcups-ufr2-uk_*_arm64.deb
#     ./scripts/setup-printer.sh ~/cnrdrvcups-ufr2-uk_*_arm64.deb      (same thing)
#
#   HP DeskJet 3835 (USB or Wi-Fi), uses the open-source HPLIP driver:
#     ./scripts/setup-printer.sh hp
#
#   Any other printer: set the variables below yourself, e.g.
#     QUEUE_NAME=Brother_HL URI_MATCH=Brother MODEL_MATCH='HL-L2350' ./scripts/setup-printer.sh custom
#
# Optional env vars (override the preset):
#   QUEUE_NAME   CUPS name for the printer (letters, digits, _ - .)
#   URI_MATCH    regex picking the printer's connection from 'lpinfo -v'
#   MODEL_MATCH  regex picking the driver from 'lpinfo -m'
#   PPD          exact driver/model name to use (skips MODEL_MATCH)
#   SET_DEFAULT  1 to make it the default printer
set -euo pipefail

PRESET="canon"
DRIVER_DEB=""
for arg in "$@"; do
  case "$arg" in
    canon|hp|custom) PRESET="$arg" ;;
    *.deb) DRIVER_DEB="$arg" ;;
    *) echo "Unknown argument: $arg (use: canon [driver.deb] | hp | custom)" >&2; exit 1 ;;
  esac
done

case "$PRESET" in
  canon)
    : "${QUEUE_NAME:=Canon_MF4820d}"
    : "${URI_MATCH:=usb://Canon}"
    : "${MODEL_MATCH:=MF4800|MF4820}"
    : "${SET_DEFAULT:=1}" ;;
  hp)
    : "${QUEUE_NAME:=HP_DeskJet_3835}"
    : "${URI_MATCH:=HP|Hewlett|DeskJet}"
    : "${MODEL_MATCH:=deskjet.?38(30|35)}"
    : "${SET_DEFAULT:=0}" ;;
  custom)
    if [[ -z "${QUEUE_NAME:-}" || -z "${URI_MATCH:-}" || ( -z "${MODEL_MATCH:-}" && -z "${PPD:-}" ) ]]; then
      echo "custom needs QUEUE_NAME, URI_MATCH and MODEL_MATCH (or PPD)." >&2
      exit 1
    fi
    : "${SET_DEFAULT:=0}" ;;
esac

if ! [[ "$QUEUE_NAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "QUEUE_NAME may only contain letters, digits, _ - ." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
if [[ -n "$DRIVER_DEB" ]]; then
  echo "==> Installing driver package $DRIVER_DEB"
  sudo apt-get install -y "$(realpath "$DRIVER_DEB")"
  sudo systemctl restart cups
fi

if [[ "$PRESET" == "hp" ]] && ! dpkg -s hplip >/dev/null 2>&1; then
  echo "==> Installing the HP driver (HPLIP)"
  sudo apt-get install -y --no-install-recommends hplip
  sudo systemctl restart cups
fi

# ---------------------------------------------------------------------------
# Connection
#   1. IPP over USB (the ipp-usb service). When it's running it owns the USB
#      port, so the plain usb:// and hp:/usb backends can't reach the printer.
#   2. Plain USB.
#   3. Network (Wi-Fi / Ethernet).
# ---------------------------------------------------------------------------
echo "==> Looking for the printer (this can take ~20 seconds)"
DEVICES="$(sudo lpinfo -v 2>/dev/null | awk '{print $2}' | grep -iE "$URI_MATCH" || true)"
URI=""
for pattern in '\(USB\)\._ipp' '^usb:' '^hp:/usb' '^(ipps?|dnssd):' '^hp:'; do
  URI="$(grep -m1 -E "$pattern" <<<"$DEVICES" || true)"
  [[ -n "$URI" ]] && break
done
if [[ -z "$URI" ]]; then
  cat >&2 <<EOF
No printer matching '$URI_MATCH' was found.
 - USB: check the cable and that the printer is switched on.
 - Wi-Fi: the printer must be on the same network as the Pi.
Then list what the Pi can see with:  sudo lpinfo -v
EOF
  exit 1
fi
echo "    found $URI"

# ---------------------------------------------------------------------------
# Driver model (PPD)
# ---------------------------------------------------------------------------
echo "==> Looking for a driver"
if [[ -n "${PPD:-}" ]]; then
  MODEL="$PPD"
elif [[ "$URI" =~ ^(ipps?|dnssd): ]]; then
  # IPP printers (network or IPP over USB) work driverless (IPP Everywhere / AirPrint).
  MODEL="everywhere"
else
  # Classic USB: use a real driver. "driverless:" entries only work with IPP URIs.
  MODEL="$(lpinfo -m 2>/dev/null | grep -iE "$MODEL_MATCH" | grep -v -e '^driverless:' -e 'hpijs' \
           | head -n1 | awk '{print $1}')"
fi
if [[ -z "$MODEL" ]]; then
  if [[ "$PRESET" == "canon" ]]; then
    cat >&2 <<'EOF'
No Canon MF4800-series driver is installed.

 1. On Canon's support site, search "MF4820d" -> Drivers -> Linux and download
    "UFR II/UFRII LT Printer Driver for Linux" (V5.x or newer includes ARM64 builds).
 2. Extract it and copy the *arm64.deb (e.g. cnrdrvcups-ufr2-uk_*_arm64.deb) to the Pi.
 3. Re-run:  ./scripts/setup-printer.sh canon path/to/that.deb

Check the Pi is on 64-bit Raspberry Pi OS with:  dpkg --print-architecture   (should print arm64)
EOF
  else
    echo "No driver matching '$MODEL_MATCH'. See the options with:  lpinfo -m | grep -i <model>" >&2
    echo "then re-run with PPD=<first column of that line>." >&2
  fi
  exit 1
fi
echo "    using $MODEL"

# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------
echo "==> Creating CUPS printer '$QUEUE_NAME'"
sudo lpadmin -p "$QUEUE_NAME" -E -v "$URI" -m "$MODEL" \
  -o media=A4 -o PageSize=A4 -o printer-is-shared=true
if [[ "$SET_DEFAULT" == "1" ]]; then
  sudo lpadmin -d "$QUEUE_NAME"
fi
sudo cupsenable "$QUEUE_NAME"
sudo cupsaccept "$QUEUE_NAME"

# Also share the printers on the LAN so Windows/Mac/phones can add them directly.
sudo cupsctl --share-printers

echo "==> Done. Status:"
lpstat -p "$QUEUE_NAME"
echo
echo "Test page:  lp -d $QUEUE_NAME /usr/share/cups/data/testprint"
echo "It now appears in the Printer list on http://$(hostname).local"

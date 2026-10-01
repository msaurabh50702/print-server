#!/usr/bin/env bash
# Serve the print server over HTTPS on the home network, so phones can
# install it as an app ("Install app" in Chrome needs https).
#
# Uses Caddy as a front door: it creates the Pi's own certificate authority
# (CA), issues a certificate for <hostname>.local and the Pi's IP address,
# and forwards requests to the print server. Plain http:// keeps working.
# Each phone installs the CA certificate once (the app's "Install app" page
# offers it for download at /ca.crt).
#
#   ./scripts/enable-https.sh                    set up / refresh (run again if the Pi's IP changes)
#   ./scripts/enable-https.sh --disable          go back to plain http on port 80
#   ./scripts/enable-https.sh --print-caddyfile  show the Caddy config it would write
set -euo pipefail

ENV_FILE=/etc/default/print-server
CA_DEST=/etc/print-server/ca.crt
CADDY_DATA=/var/lib/caddy/.local/share/caddy
CADDY_CA=$CADDY_DATA/pki/authorities/local/root.crt
APP_PORT=8080

HOST_NAME="${HOST_NAME:-$(hostname).local}"
HOST_IPS="${HOST_IPS:-$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | tr '\n' ' ')}"

caddyfile() {
  local sites="https://$HOST_NAME"
  for ip in $HOST_IPS; do sites+=", https://$ip"; done
  cat <<EOF
# Written by print-server/scripts/enable-https.sh
{
	# Keep plain http:// working for phones that haven't installed the certificate.
	auto_https disable_redirects
	# The CA is only for phones on this network; don't add it to the Pi's own trust store.
	skip_install_trust
}

http:// {
	reverse_proxy 127.0.0.1:$APP_PORT
}

$sites {
	# Caddy's own certificates normally last 12 hours. A Pi without internet
	# time has a clock that's behind after a power cut, and phones then see an
	# expired certificate; 6 days (just under the 7-day intermediate) is safe.
	tls {
		issuer internal {
			lifetime 144h
		}
	}
	reverse_proxy 127.0.0.1:$APP_PORT
}
EOF
}

set_env() {  # set_env KEY VALUE  -> update or add a line in $ENV_FILE
  if sudo grep -qE "^$1=" "$ENV_FILE" 2>/dev/null; then
    sudo sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    echo "$1=$2" | sudo tee -a "$ENV_FILE" >/dev/null
  fi
}

case "${1:-}" in
  --print-caddyfile)
    caddyfile
    exit 0 ;;
  --disable)
    echo "==> Switching back to plain http on port 80"
    sudo systemctl disable --now caddy 2>/dev/null || true
    set_env PORT 80
    set_env HOST 0.0.0.0
    sudo systemctl restart print-server
    echo "Done. The app is at http://$HOST_NAME"
    exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1" >&2; exit 1 ;;
esac

echo "==> Installing Caddy"
if ! command -v caddy >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y caddy
fi

echo "==> Moving the print server behind Caddy (127.0.0.1:$APP_PORT)"
set_env PORT "$APP_PORT"
set_env HOST 127.0.0.1
set_env CA_CERT_PATH "$CA_DEST"
sudo systemctl restart print-server

echo "==> Configuring Caddy for $HOST_NAME ${HOST_IPS}"
caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
sudo systemctl enable caddy >/dev/null 2>&1
# Site certificates are re-issued with the new lifetime; the CA phones trust stays.
sudo rm -rf "$CADDY_DATA/certificates/local"
sudo systemctl restart caddy

echo "==> Waiting for Caddy to create the certificate authority"
for _ in $(seq 1 30); do
  sudo test -f "$CADDY_CA" && break
  sleep 1
done
if ! sudo test -f "$CADDY_CA"; then
  echo "Caddy did not create its CA. Check: journalctl -u caddy -n 50" >&2
  exit 1
fi
sudo install -D -m 644 "$CADDY_CA" "$CA_DEST"
sudo systemctl restart print-server

cat <<EOF

Done! The print server now answers on both:
  http://$HOST_NAME     (works everywhere, can only add a shortcut)
  https://$HOST_NAME    (installable as an app once the phone trusts the certificate)

On each phone: open http://$HOST_NAME/install and follow the steps
(download certificate -> install it -> open the https page -> Install).
If the Pi's IP address changes, run this script again.
EOF

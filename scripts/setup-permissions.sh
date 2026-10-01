#!/usr/bin/env bash
# Let the web app do the few system tasks it needs, without a password:
#   - restart and shut down the Pi (Print queue -> Print server)
#   - set the clock from a phone when the Pi has no internet time (a Pi has no
#     clock battery, so without internet it's behind after every power cut)
#
# Run by install.sh; also safe to run on its own (needs no internet):
#   ./scripts/setup-permissions.sh
set -euo pipefail

RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
SUDOERS_TMP="$(mktemp)"
trap 'rm -f "$SUDOERS_TMP"' EXIT

cat > "$SUDOERS_TMP" <<RULES
# Written by print-server/scripts/setup-permissions.sh
$RUN_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff
$RUN_USER ALL=(root) NOPASSWD: /usr/bin/date -s @*
RULES

if sudo visudo -cf "$SUDOERS_TMP" >/dev/null; then
  sudo install -m 440 "$SUDOERS_TMP" /etc/sudoers.d/print-server
  echo "Done: the web app can restart/shut down the Pi and correct its clock."
else
  echo "Skipped: the sudoers rule failed validation." >&2
  exit 1
fi

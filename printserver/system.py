"""Restart or shut down the Pi from the web app.

The service runs as an ordinary user, so install.sh adds a sudoers rule that
allows exactly these two commands without a password.
"""
import subprocess
import threading

from .printing import PrintError

SYSTEMCTL = "/usr/bin/systemctl"
POWER_ACTIONS = {"restart": "reboot", "shutdown": "poweroff"}
DELAY_SECONDS = 1.5      # long enough for the reply to reach the phone first


def _allowed(verb):
    try:
        result = subprocess.run(["sudo", "-n", "-l", SYSTEMCTL, verb],
                                capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def power(action, dry_run=False):
    """Schedule a restart or shutdown; raises PrintError if it isn't possible."""
    verb = POWER_ACTIONS.get(action)
    if not verb:
        raise PrintError("Unknown action")
    if dry_run:
        raise PrintError("Test mode: the computer is not restarted or shut down")
    if not _allowed(verb):
        raise PrintError("Not allowed yet: run ./install.sh again on the Pi")
    timer = threading.Timer(DELAY_SECONDS, subprocess.run,
                            args=(["sudo", "-n", SYSTEMCTL, verb],))
    timer.daemon = True
    timer.start()

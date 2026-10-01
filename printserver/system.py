"""Restart or shut down the Pi, and correct its clock, from the web app.

The service runs as an ordinary user, so scripts/setup-permissions.sh adds a
sudoers rule that allows exactly these commands without a password.
"""
import subprocess
import threading
import time

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


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------
# A Raspberry Pi has no clock battery: without internet time it starts from
# the time it last saved, so after a power cut it's behind. Phones always know
# the right time, so the app sends it when the two disagree.

DATE = "/usr/bin/date"
CLOCK_TOLERANCE = 120          # seconds; smaller differences are left alone
EARLIEST, LATEST = 1.7e9, 4.1e9  # sane range for a phone's clock (2023-2099)


def clock_synchronized():
    """True if the Pi gets its time from the internet (then it's right already)."""
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out == "yes"


def set_clock(now, dry_run=False):
    """Set the clock to `now` (seconds since 1970, from a phone) if it's clearly off.

    Returns {"adjusted": bool, "offset": seconds the clock was behind}.
    """
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not EARLIEST < now < LATEST:
        raise PrintError("Invalid time")
    offset = now - time.time()
    if abs(offset) < CLOCK_TOLERANCE or dry_run or clock_synchronized():
        return {"adjusted": False, "offset": round(offset)}
    try:
        result = subprocess.run(["sudo", "-n", DATE, "-s", f"@{int(now)}"],
                                capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise PrintError("Could not set the clock")
    if result.returncode != 0:
        raise PrintError("Not allowed yet: run ./scripts/setup-permissions.sh on the Pi")
    return {"adjusted": True, "offset": round(offset)}


def boot_time():
    """When the Pi started, by its current clock."""
    try:
        with open("/proc/uptime") as f:
            return time.time() - float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None

"""Status LEDs and buttons on the Pi's GPIO header.

Runs as its own service (print-panel, see scripts/setup-panel.sh) next to the
print server and only reads its status over HTTP, so a problem here can never
stop printing.

LEDs (each through a 220-330 ohm resistor to ground):
  POWER    slow blink: the Pi is running (heartbeat)
  NETWORK  on: has an address | slow blink: cable plugged in or Wi-Fi joined,
           waiting for an address from the router | off: no cable, no Wi-Fi
  SERVER   on: the app answers | fast blink: not answering / starting
  PRINTER  on: all ready | slow blink: printing | flicker: ink, toner or paper
           low | fast blink: switched off or an error | off: none set up, or
           unknown because the server isn't answering

Buttons (between the pin and ground):
  FIX      press: restart the printing services (and reconnect the Wi-Fi if
           there's no address) | hold 5 s: restart the Pi
  POWER    hold 3 s: shut down | press while shut down: start (GPIO 3 only)

Pins are BCM numbers, changeable in /etc/default/print-server.
"""
import json
import logging
import os
import subprocess
import threading
import time
import urllib.request

log = logging.getLogger("print-panel")

PINS = {  # setting name -> default BCM pin (physical pin in brackets)
    "PANEL_LED_POWER": 5,      # (29)
    "PANEL_LED_NETWORK": 6,    # (31)
    "PANEL_LED_SERVER": 13,    # (33)
    "PANEL_LED_PRINTER": 19,   # (35)
    "PANEL_BUTTON_FIX": 26,    # (37)
    "PANEL_BUTTON_POWER": 3,   # (5)  - the only pin that can also start a halted Pi
}
POLL_SECONDS = 5
FIX_HOLD_SECONDS = 5
POWER_HOLD_SECONDS = 3
SERVICES = ["cups", "ipp-usb", "print-server", "caddy"]

# Blink patterns: (seconds on, seconds off); None = steady.
ON, OFF = "on", "off"
SLOW, FAST, FLICKER = (1.0, 1.0), (0.15, 0.15), (1.8, 0.2)


# ---------------------------------------------------------------------------
# What the LEDs show (pure functions, easy to test)
# ---------------------------------------------------------------------------

def network_pattern(has_address, wifi_joined):
    if has_address:
        return ON
    return SLOW if wifi_joined else OFF


def server_pattern(answering):
    return ON if answering else FAST


def printer_pattern(overview):
    """Pattern for all printers together, from the app's /api/printers data."""
    if not overview:
        return OFF                       # unknown: the SERVER LED shows that problem
    printers = overview.get("printers") or []
    if not printers:
        return OFF
    if any(not p.get("ok", True) or p.get("connected") is False
           or any(a.get("severity") == "error" for a in p.get("alerts") or [])
           for p in printers):
        return FAST
    if any(p.get("queued") for p in printers):
        return SLOW
    if any(p.get("alerts") for p in printers):
        return FLICKER
    return ON


# ---------------------------------------------------------------------------
# Reading the state of the Pi
# ---------------------------------------------------------------------------

def _run(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("%s failed: %s", args[0], exc)
        return None


def _link_up(device):
    """True if the network cable is plugged in (eth0) or the Wi-Fi is joined (wlan0)."""
    try:
        with open(f"/sys/class/net/{device}/operstate") as f:
            return f.read().strip() == "up"
    except OSError:
        return False


def network_state():
    """(has an IPv4 address, linked: cable plugged in or Wi-Fi joined)."""
    result = _run(["ip", "-4", "-o", "addr", "show", "scope", "global"])
    has_address = bool(result and result.stdout.strip())
    return has_address, _link_up("eth0") or _link_up("wlan0")


def app_url(path):
    port = os.environ.get("PORT", "8080")
    return f"http://127.0.0.1:{port}{path}"


def fetch_json(path, timeout=4):
    try:
        with urllib.request.urlopen(app_url(path), timeout=timeout) as res:
            return json.loads(res.read())
    except (OSError, ValueError):
        return None


def caddy_problem():
    """True if HTTPS is set up (Caddy enabled) but Caddy isn't running."""
    enabled = _run(["systemctl", "is-enabled", "--quiet", "caddy"])
    if not enabled or enabled.returncode != 0:
        return False
    active = _run(["systemctl", "is-active", "--quiet", "caddy"])
    return not active or active.returncode != 0


# ---------------------------------------------------------------------------
# Button actions
# ---------------------------------------------------------------------------

def restart_services(run=_run):
    """The fix-it button: reconnect Wi-Fi if needed, then restart the printing services."""
    has_address, _ = network_state()
    if not has_address:
        # Reconnect whichever is in use: the cable if it's plugged in, else Wi-Fi.
        device = "eth0" if _link_up("eth0") else "wlan0"
        log.info("no network address: reconnecting %s", device)
        run(["nmcli", "device", "disconnect", device], timeout=20)
        run(["nmcli", "device", "connect", device], timeout=60)
    for service in SERVICES:
        check = run(["systemctl", "cat", service])
        if check and check.returncode == 0:   # skip services that aren't installed
            log.info("restarting %s", service)
            run(["systemctl", "restart", service], timeout=60)


def reboot(run=_run):
    run(["systemctl", "reboot"])


def shutdown(run=_run):
    run(["systemctl", "poweroff"])


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------

class Panel:
    def __init__(self, pins=None, run=_run, pin_factory=None):
        from gpiozero import LED, Button  # imported here so the app and tests don't need it
        pins = pins or {name: int(os.environ.get(name, default)) for name, default in PINS.items()}
        kw = {"pin_factory": pin_factory} if pin_factory else {}
        self.run = run
        self.leds = {
            "power": LED(pins["PANEL_LED_POWER"], **kw),
            "network": LED(pins["PANEL_LED_NETWORK"], **kw),
            "server": LED(pins["PANEL_LED_SERVER"], **kw),
            "printer": LED(pins["PANEL_LED_PRINTER"], **kw),
        }
        self.patterns = {}
        self.busy = threading.Lock()   # one button action at a time
        self.fix_was_held = False

        self.fix = Button(pins["PANEL_BUTTON_FIX"], hold_time=FIX_HOLD_SECONDS, **kw)
        self.fix.when_held = self._fix_held
        self.fix.when_released = self._fix_released
        self.power = Button(pins["PANEL_BUTTON_POWER"], hold_time=POWER_HOLD_SECONDS, **kw)
        self.power.when_held = lambda: self._action("shutdown", shutdown)
        self.show("power", SLOW)

    def show(self, name, pattern):
        """Set an LED, restarting a blink only when its pattern changes."""
        if self.patterns.get(name) == pattern:
            return
        self.patterns[name] = pattern
        led = self.leds[name]
        if pattern == ON:
            led.on()
        elif pattern == OFF:
            led.off()
        else:
            led.blink(on_time=pattern[0], off_time=pattern[1])

    def _fix_held(self):
        self.fix_was_held = True           # so letting go doesn't also restart services
        self._action("reboot", reboot)

    def _fix_released(self):
        if self.fix_was_held:
            self.fix_was_held = False
            return
        self._action("restart", restart_services)

    def _action(self, name, action):
        if not self.busy.acquire(blocking=False):
            return
        log.info("button: %s", name)
        # Feedback: every LED on, so you can see the press was taken.
        for led_name in self.leds:
            self.show(led_name, ON)

        def work():
            try:
                action(self.run)
            finally:
                self.patterns.clear()          # let update() redraw everything
                self.show("power", SLOW)
                self.busy.release()
        threading.Thread(target=work, daemon=True).start()

    def update(self):
        if self.busy.locked():
            return
        has_address, wifi_joined = network_state()
        answering = fetch_json("/api/ping") is not None and not caddy_problem()
        overview = fetch_json("/api/printers", timeout=15) if answering else None
        self.show("network", network_pattern(has_address, wifi_joined))
        self.show("server", server_pattern(answering))
        self.show("printer", printer_pattern(overview))

    def run_forever(self):
        while True:
            try:
                self.update()
            except Exception:  # keep the lights going whatever happens
                log.exception("update failed")
            time.sleep(POLL_SECONDS)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    Panel().run_forever()


if __name__ == "__main__":
    main()

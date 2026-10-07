"""Status LEDs and buttons (printserver/panel.py), on gpiozero's mock pins."""
import time

import pytest

from printserver import panel

gpiozero = pytest.importorskip("gpiozero")
from gpiozero.pins.mock import MockFactory, MockPWMPin  # noqa: E402

PINS = {name: default for name, default in panel.PINS.items()}


def printer(**kw):
    return {"name": "P", "ok": True, "connected": True, "queued": 0, "alerts": [], **kw}


def test_led_patterns():
    assert panel.network_pattern(True, True) == panel.ON
    assert panel.network_pattern(False, True) == panel.SLOW      # joined, no address yet
    assert panel.network_pattern(False, False) == panel.OFF
    assert panel.server_pattern(True) == panel.ON
    assert panel.server_pattern(False) == panel.FAST

    pattern = lambda *printers: panel.printer_pattern({"printers": list(printers)})
    assert panel.printer_pattern(None) == panel.OFF
    assert pattern() == panel.OFF
    assert pattern(printer()) == panel.ON
    assert pattern(printer(queued=2)) == panel.SLOW
    assert pattern(printer(alerts=[{"severity": "warning", "text": "Toner low"}])) == panel.FLICKER
    assert pattern(printer(), printer(connected=False)) == panel.FAST
    assert pattern(printer(alerts=[{"severity": "error", "text": "Out of paper"}])) == panel.FAST
    assert pattern(printer(ok=False)) == panel.FAST


@pytest.fixture
def board(monkeypatch):
    factory = MockFactory(pin_class=MockPWMPin)
    commands = []
    monkeypatch.setattr(panel, "network_state", lambda: (True, True))
    done = type("Done", (), {"returncode": 0, "stdout": ""})()
    p = panel.Panel(pins=PINS, run=lambda args, **kw: commands.append(args) or done, pin_factory=factory)
    yield p, factory, commands
    for device in [*p.leds.values(), p.fix, p.power]:
        device.close()


def wait_for(check, seconds=3):
    end = time.time() + seconds
    while time.time() < end and not check():
        time.sleep(0.02)
    return check()


def test_update_lights_leds(board, monkeypatch):
    p, factory, _ = board
    monkeypatch.setattr(panel, "network_state", lambda: (False, True))
    monkeypatch.setattr(panel, "caddy_problem", lambda: False)
    monkeypatch.setattr(panel, "fetch_json",
                        lambda path, timeout=4: {"ok": True} if path == "/api/ping"
                        else {"printers": [printer()]})
    p.update()
    assert p.patterns == {"power": panel.SLOW, "network": panel.SLOW,
                          "server": panel.ON, "printer": panel.ON}
    assert p.leds["server"].is_lit and p.leds["printer"].is_lit

    monkeypatch.setattr(panel, "fetch_json", lambda path, timeout=4: None)
    p.update()
    assert p.patterns["server"] == panel.FAST and p.patterns["printer"] == panel.OFF
    assert not p.leds["printer"].is_lit


def test_fix_button_press_restarts_services(board, monkeypatch):
    p, factory, commands = board
    pin = factory.pin(PINS["PANEL_BUTTON_FIX"])
    pin.drive_low()
    time.sleep(0.1)
    pin.drive_high()
    assert wait_for(lambda: ["systemctl", "restart", "print-server"] in commands)
    assert ["systemctl", "reboot"] not in commands
    assert wait_for(lambda: not p.busy.locked())


def test_fix_button_hold_reboots_only(board, monkeypatch):
    p, factory, commands = board
    monkeypatch.setattr(p.fix, "hold_time", 0.2)
    pin = factory.pin(PINS["PANEL_BUTTON_FIX"])
    pin.drive_low()
    assert wait_for(lambda: ["systemctl", "reboot"] in commands)
    pin.drive_high()
    time.sleep(0.2)
    assert not any(c[:2] == ["systemctl", "restart"] for c in commands)
    assert ["systemctl", "cat", "cups"] not in commands


def test_power_button_hold_shuts_down(board, monkeypatch):
    p, factory, commands = board
    monkeypatch.setattr(p.power, "hold_time", 0.2)
    pin = factory.pin(PINS["PANEL_BUTTON_POWER"])
    pin.drive_low()
    time.sleep(0.05)
    pin.drive_high()            # a short press does nothing
    time.sleep(0.3)
    assert commands == []
    pin.drive_low()
    assert wait_for(lambda: commands == [["systemctl", "poweroff"]])
    pin.drive_high()


def test_restart_reconnects_wifi_without_address(monkeypatch):
    commands = []
    run = lambda args, **kw: commands.append(args) or type("R", (), {"returncode": 0})()
    monkeypatch.setattr(panel, "network_state", lambda: (False, True))
    panel.restart_services(run)
    assert commands[0] == ["nmcli", "device", "disconnect", "wlan0"]
    assert ["systemctl", "restart", "cups"] in commands

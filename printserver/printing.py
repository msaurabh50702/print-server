"""Thin wrapper around the CUPS command line tools (lp, lpstat, lpoptions, cancel).

Reading CUPS state means starting several processes, which is slow on a
Raspberry Pi, so printer/job state is read in one go into a short-lived
snapshot shared by all requests, and printer capabilities (which only change
when a printer is reconfigured) are cached separately.
"""
import copy
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

# CUPS queue names: letters, digits, _ - . (no spaces or slashes).
PRINTER_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,127}$")
CUPS_JOB_ID_RE = re.compile(r"^([A-Za-z0-9_.\-]{1,127})-(\d{1,9})$")

SNAPSHOT_TTL = 2.0        # seconds printer/job state is reused between requests
CAPS_TTL = 600.0          # fallback lifetime of capabilities when the PPD can't be stat'ed
PPD_DIR = Path("/etc/cups/ppd")

# Printers shown in dry-run mode, so the UI can be tried without CUPS.
DRY_RUN_PRINTERS = [
    {"name": "Test_Mono_Laser", "description": "Test mono laser (dry run)",
     "state": "idle", "ok": True, "is_default": True, "duplex": True, "color": False,
     "paper": None,
     "quality": {"key": "CNTonerSaving", "default": "False", "choices": [
         {"value": "False", "label": "Normal"}, {"value": "True", "label": "Toner save"}]},
     "supplies": [{"name": "Black toner", "level": 64, "color": "#000000", "low": False}],
     "alerts": []},
    {"name": "Test_Colour_Inkjet", "description": "Test colour inkjet (dry run)",
     "state": "idle", "ok": True, "is_default": False, "duplex": False, "color": True,
     "paper": {"key": "MediaType", "default": "Stationery", "choices": [
         {"value": "Stationery", "label": "Plain paper"},
         {"value": "PhotographicGlossy", "label": "Photo glossy"}]},
     "quality": {"key": "cupsPrintQuality", "default": "Normal", "choices": [
         {"value": "Draft", "label": "Draft (saves ink)"}, {"value": "Normal", "label": "Normal"},
         {"value": "High", "label": "Best"}]},
     "supplies": [{"name": "Black ink", "level": 12, "color": "#000000", "low": False},
                  {"name": "Tri-color ink", "level": 45, "color": "multi", "low": False}],
     "alerts": []},
]


class PrintError(Exception):
    pass


def _run(args, timeout=20):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise PrintError(f"'{args[0]}' not found - is CUPS installed? (sudo apt install cups)")
    except subprocess.TimeoutExpired:
        raise PrintError(f"'{args[0]}' timed out")
    return result


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_lpstat_printers(text):
    """Parse `lpstat -l -p` into [{name, description, state, ok, printing_job}]."""
    printers, current = [], None
    for line in text.splitlines():
        match = re.match(r"^printer (\S+) (.*)$", line)
        if match:
            name, rest = match.groups()
            job = re.search(r"now printing (\S+?)\.?(\s|$)", rest)
            if "disabled" in rest:
                state = "disabled"
            elif job:
                state = "printing"
            else:
                state = "idle"
            current = {"name": name, "description": name, "state": state,
                       "ok": state != "disabled", "printing_job": job.group(1) if job else None}
            printers.append(current)
            continue
        match = re.match(r"^\s+Description:\s*(.+)$", line)
        if match and current:
            current["description"] = match.group(1).strip()
    return printers


def parse_lpstat_jobs(text):
    """Parse `lpstat -o` (all queued jobs, in queue order)."""
    jobs = []
    for line in text.splitlines():
        match = re.match(r"^(\S+)\s+(\S+)\s+(\d+)\s+(.*)$", line)
        if not match:
            continue
        job_id, owner, size, _date = match.groups()
        id_match = CUPS_JOB_ID_RE.match(job_id)
        if not id_match:
            continue
        jobs.append({"id": job_id, "printer": id_match.group(1), "owner": owner,
                     "size": int(size), "title": "", "state": "waiting"})
    return jobs


# Friendly names for common driver choices; anything else is prettified.
PAPER_LABELS = {
    "stationery": "Plain paper", "plain": "Plain paper", "plainpaper": "Plain paper",
    "auto": "Automatic", "photographic": "Photo paper", "photographicglossy": "Photo glossy",
    "photographichighgloss": "Photo high gloss", "photographicsemigloss": "Photo semi-gloss",
    "photographicmatte": "Photo matte", "com.hp.specialtyglossy": "HP glossy",
    "com.hp.specialtymatte": "HP matte", "com.hp.advanced-photo": "HP advanced photo",
    "transparency": "Transparency", "envelope": "Envelope", "labels": "Labels",
    "cardstock": "Card", "heavy": "Heavy paper", "thick": "Thick paper", "recycled": "Recycled paper",
}
QUALITY_KEYS = ("cupsPrintQuality", "print-quality", "OutputMode")
QUALITY_LABELS = {
    "draft": "Draft (saves ink)", "fastdraft": "Draft (saves ink)", "normal": "Normal",
    "high": "Best", "best": "Best", "photo": "Photo", "3": "Draft (saves ink)", "4": "Normal", "5": "Best",
}


def _pretty(value):
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", value.split(".")[-1]).replace("-", " ").replace("_", " ")
    return words[:1].upper() + words[1:]


def _choice_set(key, values, default, labels):
    choices, seen = [], set()
    for value in values:
        label = labels.get(value.lower()) or _pretty(value)
        if label in seen:
            continue
        seen.add(label)
        choices.append({"value": value, "label": label})
    return {"key": key, "default": default, "choices": choices} if len(choices) > 1 else None


def parse_lpoptions(text):
    """Work out printer features from `lpoptions -p NAME -l` output.

    Returns {"duplex": bool, "color": bool, "mono_option": (key, value) | None,
             "paper": choice-set | None, "quality": choice-set | None}
    where a choice-set is {"key", "default", "choices": [{"value", "label"}]}.
    """
    options, defaults = {}, {}
    for line in text.splitlines():
        match = re.match(r"^([^/:\s]+)(?:/[^:]*)?:\s*(.*)$", line)
        if match:
            key, values = match.groups()
            options[key] = [v.lstrip("*") for v in values.split()]
            marked = [v[1:] for v in values.split() if v.startswith("*")]
            defaults[key] = marked[0] if marked else (options[key][0] if options[key] else None)

    duplex = False
    for key in ("Duplex", "sides", "EFDuplex", "KMDuplex"):
        values = options.get(key, [])
        if any(re.search(r"DuplexNoTumble|DuplexTumble|two-sided", v, re.I) for v in values):
            duplex = True

    color, mono_option = False, None
    for key in ("ColorModel", "print-color-mode", "ColorMode", "OutputMode", "CNColorMode"):
        values = options.get(key, [])
        grey = [v for v in values if re.search(r"gr[ae]y|mono|black|^k", v, re.I)]
        colourful = [v for v in values if v not in grey]
        if grey and colourful:
            color = True
            # Prefer plain black ink ("KGray"/"Gray") for black & white.
            grey.sort(key=lambda v: (not re.fullmatch(r"k?gr[ae]y", v, re.I), v))
            mono_option = (key, grey[0])
            break

    paper = None
    if options.get("MediaType"):
        paper = _choice_set("MediaType", options["MediaType"], defaults["MediaType"], PAPER_LABELS)

    quality = None
    for key in QUALITY_KEYS:
        if options.get(key):
            quality = _choice_set(key, options[key], defaults[key], QUALITY_LABELS)
            break
    if quality is None and {"True", "False"} <= set(options.get("CNTonerSaving", [])):
        # Canon laser drivers: "toner save" is the draft mode.
        quality = {"key": "CNTonerSaving", "default": defaults["CNTonerSaving"], "choices": [
            {"value": "False", "label": "Normal"}, {"value": "True", "label": "Toner save"}]}

    return {"duplex": duplex, "color": color, "mono_option": mono_option,
            "paper": paper, "quality": quality}


# ---------------------------------------------------------------------------
# Ink / toner levels and printer alerts (IPP Get-Printer-Attributes to CUPS)
# ---------------------------------------------------------------------------

CUPS_IPP_URL = "http://localhost:631/printers/{}"
IPP_ATTRIBUTES = ("marker-names", "marker-levels", "marker-colors", "marker-types",
                  "marker-low-levels", "printer-state-reasons")
_IPP_STRING_TAGS = {0x41, 0x42, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49}

# printer-state-reasons keyword prefix -> message
REASON_TEXT = [
    ("media-empty", "Out of paper"), ("media-needed", "Load paper"), ("media-jam", "Paper jam"),
    ("media-low", "Paper low"), ("toner-empty", "Toner empty"), ("toner-low", "Toner low"),
    ("marker-supply-empty", "Ink or toner empty"), ("marker-supply-low", "Ink or toner low"),
    ("marker-waste-full", "Waste ink full"), ("marker-waste-almost-full", "Waste ink almost full"),
    ("door-open", "Cover open"), ("cover-open", "Cover open"), ("interlock-open", "Cover open"),
    ("input-tray-missing", "Paper tray missing"), ("output-area-full", "Output tray full"),
    ("offline", "Printer not connected"), ("shutdown", "Printer switched off"),
    ("cups-missing-filter", "Printer driver problem"),
]


def _ipp_attribute(tag, name, value):
    name, value = name.encode(), value.encode()
    return struct.pack(">BH", tag, len(name)) + name + struct.pack(">H", len(value)) + value


def build_ipp_request(printer):
    body = struct.pack(">BBHI", 2, 0, 0x000B, 1) + b"\x01"
    body += _ipp_attribute(0x47, "attributes-charset", "utf-8")
    body += _ipp_attribute(0x48, "attributes-natural-language", "en")
    body += _ipp_attribute(0x45, "printer-uri", f"ipp://localhost/printers/{printer}")
    for i, attr in enumerate(IPP_ATTRIBUTES):
        body += _ipp_attribute(0x44, "requested-attributes" if i == 0 else "", attr)
    return body + b"\x03"


def parse_ipp_response(data):
    """Decode an IPP response into {name: [values]} (only the parts we need)."""
    if len(data) < 8 or struct.unpack(">H", data[2:4])[0] >= 0x0100:
        return {}
    attrs, pos, name = {}, 8, None
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == 0x03:
            break
        if tag < 0x10:            # start of an attribute group
            continue
        name_len = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if name_len:
            name = data[pos:pos + name_len].decode("utf-8", "replace")
        pos += name_len
        value_len = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        raw = data[pos:pos + value_len]
        pos += value_len
        if tag in (0x21, 0x23) and value_len == 4:
            value = struct.unpack(">i", raw)[0]
        elif tag in _IPP_STRING_TAGS:
            value = raw.decode("utf-8", "replace")
        else:
            continue
        attrs.setdefault(name, []).append(value)
    return attrs


def ipp_printer_attributes(printer, timeout=2.0):
    """Ask CUPS for a printer's marker levels and state reasons. Never raises."""
    request = urllib.request.Request(
        CUPS_IPP_URL.format(printer), data=build_ipp_request(printer),
        headers={"Content-Type": "application/ipp"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return parse_ipp_response(response.read())
    except (OSError, ValueError, struct.error):
        return {}


def supplies_and_alerts(attrs):
    """Turn IPP attributes into ink/toner bars and human-readable alerts."""
    names = attrs.get("marker-names", [])
    levels = attrs.get("marker-levels", [])
    colors = attrs.get("marker-colors", [])
    lows = attrs.get("marker-low-levels", [])
    supplies = []
    for i, name in enumerate(names):
        level = levels[i] if i < len(levels) else -1
        color = colors[i] if i < len(colors) else ""
        hexes = re.findall(r"#[0-9A-Fa-f]{6}", color)
        supplies.append({
            "name": name.replace("_", " ").strip(),
            "level": level if 0 <= level <= 100 else None,
            "color": hexes[0] if len(hexes) == 1 else ("multi" if hexes else "#555555"),
            "low": 0 <= level <= (lows[i] if i < len(lows) and lows[i] > 0 else 10),
        })

    alerts = []
    for reason in attrs.get("printer-state-reasons", []):
        if reason == "none":
            continue
        severity = "error" if reason.endswith("-error") else "warning" if reason.endswith("-warning") else None
        base = re.sub(r"-(error|warning|report)$", "", reason)
        if base == "offline" or base == "cups-missing-filter":
            severity = "error"
        text = next((t for prefix, t in REASON_TEXT if base == prefix), None)
        if text and severity and not any(a["text"] == text for a in alerts):
            alerts.append({"text": text, "severity": severity})
    for supply in supplies:
        if supply["low"] and not any("low" in a["text"].lower() or "empty" in a["text"].lower() for a in alerts):
            alerts.append({"text": f"{supply['name']} low", "severity": "warning"})
    alerts.sort(key=lambda a: a["severity"] != "error")
    return supplies, alerts


# ---------------------------------------------------------------------------
# Cached state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_snapshot = {"time": 0.0, "data": None}
_caps_cache = {}   # name -> (ppd_mtime, fetched_at, caps)


def _ppd_mtime(name):
    try:
        return os.stat(PPD_DIR / f"{name}.ppd").st_mtime
    except OSError:
        return None


def printer_capabilities(name):
    """Capabilities of one printer, cached until its PPD changes.

    Falls back to 'duplex allowed, mono only' when the options can't be read.
    """
    mtime = _ppd_mtime(name)
    cached = _caps_cache.get(name)
    if cached:
        cached_mtime, fetched, caps = cached
        if cached_mtime == mtime and (mtime is not None or time.time() - fetched < CAPS_TTL):
            return caps
    result = _run(["lpoptions", "-p", name, "-l"])
    if result.returncode != 0 or not result.stdout.strip():
        caps = {"duplex": True, "color": False, "mono_option": None, "paper": None, "quality": None}
    else:
        caps = parse_lpoptions(result.stdout)
    _caps_cache[name] = (mtime, time.time(), caps)
    return caps


def _read_cups():
    printers = parse_lpstat_printers(_run(["lpstat", "-l", "-p"]).stdout)
    default = re.search(r"system default destination:\s*(\S+)", _run(["lpstat", "-d"]).stdout)
    default = default.group(1) if default else None
    jobs = parse_lpstat_jobs(_run(["lpstat", "-o"]).stdout) if printers else []

    positions = {}
    printing = {p["printing_job"] for p in printers if p["printing_job"]}
    for job in jobs:
        if job["id"] in printing:
            job["state"], job["rank"] = "printing", 0
        else:
            positions[job["printer"]] = positions.get(job["printer"], 0) + 1
            job["rank"] = positions[job["printer"]]

    for printer in printers:
        caps = printer_capabilities(printer["name"])
        supplies, alerts = supplies_and_alerts(ipp_printer_attributes(printer["name"]))
        printer.update(is_default=printer["name"] == default, duplex=caps["duplex"],
                       color=caps["color"], paper=caps["paper"], quality=caps["quality"],
                       supplies=supplies, alerts=alerts,
                       queued=sum(1 for j in jobs if j["printer"] == printer["name"]))
        del printer["printing_job"]
    return {"printers": printers, "default": default, "jobs": jobs}


def snapshot(dry_run=False, fresh=False):
    """Printers (with capabilities) and active jobs. Never raises.

    Results are shared for SNAPSHOT_TTL seconds; concurrent callers wait for a
    single refresh instead of each starting their own lpstat processes.
    """
    if dry_run:
        return {"printers": [dict(p, queued=0) for p in DRY_RUN_PRINTERS],
                "default": "Test_Mono_Laser", "jobs": []}
    with _lock:
        data = _snapshot["data"]
        if fresh or data is None or time.time() - _snapshot["time"] > SNAPSHOT_TTL:
            try:
                data = _read_cups()
            except PrintError:
                data = {"printers": [], "default": None, "jobs": []}
            _snapshot.update(time=time.time(), data=data)
        return copy.deepcopy(data)


def invalidate():
    """Forget the snapshot, e.g. right after a job was sent or cancelled."""
    with _lock:
        _snapshot["data"] = None


def list_printers(dry_run=False):
    return snapshot(dry_run)["printers"]


def active_jobs(dry_run=False, fresh=False):
    return snapshot(dry_run, fresh)["jobs"]


def resolve_printer(requested, configured, dry_run=False, state=None):
    """Pick the printer for a job and check it exists.

    requested: printer chosen in the UI (may be empty).
    configured: PRINTER_NAME from the settings (may be empty).
    """
    state = state or snapshot(dry_run)
    names = [p["name"] for p in state["printers"]]
    if requested:
        if not PRINTER_NAME_RE.match(requested) or requested not in names:
            raise PrintError(f"Printer '{requested}' is not available")
        return requested
    if configured and configured in names:
        return configured
    if state["default"] in names:
        return state["default"]
    if names:
        return names[0]
    raise PrintError("No printer configured. Run scripts/setup-printer.sh on the Pi.")


def status_label(printer, dry_run=False):
    """Short text + ok flag for the status pill."""
    if not printer:
        return "No printer", False
    if dry_run:
        return "Test mode", True
    alerts = printer.get("alerts") or []
    if not printer["ok"]:
        return "Offline", False
    if alerts and alerts[0]["severity"] == "error":
        return alerts[0]["text"], False
    if printer["queued"]:
        return f"Printing ({printer['queued']})", True
    return (alerts[0]["text"] if alerts else "Ready"), True


def printer_status(requested, configured, dry_run=False):
    """Status of the chosen (or default) printer. Never raises."""
    state = snapshot(dry_run)
    if not state["printers"]:
        return {"ok": False, "printer": None, "printers": 0,
                "message": "No printer configured. Run scripts/setup-printer.sh on the Pi."}
    try:
        name = resolve_printer(requested, configured, dry_run, state)
    except PrintError:
        name = resolve_printer("", configured, dry_run, state)
    printer = next(p for p in state["printers"] if p["name"] == name)
    return {"ok": printer["ok"], "printer": name, "description": printer["description"],
            "state": printer["state"], "printers": len(state["printers"]),
            "queued_jobs": printer["queued"],
            "message": f"{printer['description']}: {printer['state']}"}


def cancel_job(job_id):
    """Cancel an active job. Only jobs currently in the queue can be cancelled."""
    if not CUPS_JOB_ID_RE.match(job_id or ""):
        raise PrintError("Invalid job")
    if job_id not in {job["id"] for job in active_jobs(fresh=True)}:
        raise PrintError("This job has already finished or was cancelled")
    result = _run(["cancel", job_id])
    invalidate()
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or "Could not cancel the job")


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def parse_page_ranges(text, page_count):
    """Validate a page range string like '1-3,5' and return it normalised.

    Returns None for "all pages". Raises ValueError on bad input.
    """
    text = (text or "").replace(" ", "")
    if not text:
        return None
    parts = []
    for chunk in text.split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", chunk)
        if not match:
            raise ValueError(f"Invalid page range '{chunk}'")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > page_count:
            raise ValueError(f"Page range '{chunk}' is outside 1-{page_count}")
        parts.append(f"{start}-{end}" if end != start else str(start))
    return ",".join(parts)


def build_lp_args(pdf_path, printer, copies=1, page_ranges=None, duplex=False,
                  fit_to_page=False, title=None, mono=False, mono_option=None, extra_options=()):
    args = ["lp", "-d", printer, "-n", str(copies), "-o", "media=A4"]
    args += ["-o", "sides=two-sided-long-edge" if duplex else "sides=one-sided"]
    if copies > 1:
        args += ["-o", "collate=true"]
    if page_ranges:
        args += ["-P", page_ranges]
    if fit_to_page:
        args += ["-o", "fit-to-page", "-o", "print-scaling=fit"]
    else:
        # Exactly as laid out (real-size ID cards, passport photos, "Actual size").
        args += ["-o", "print-scaling=none"]
    for key, value in extra_options:
        args += ["-o", f"{key}={value}"]
    if mono:
        args += ["-o", "print-color-mode=monochrome"]
        if mono_option:
            # Driver-specific setting (e.g. HP "ColorModel=KGray") for drivers
            # that ignore the standard print-color-mode option.
            args += ["-o", f"{mono_option[0]}={mono_option[1]}"]
    if title:
        args += ["-t", title[:100]]
    args.append(str(pdf_path))
    return args


def driver_options(caps, paper=None, quality=None):
    """Validated (key, value) pairs for the chosen paper type / print quality."""
    chosen = []
    for choice_set, value in ((caps.get("paper"), paper), (caps.get("quality"), quality)):
        if choice_set and value and value in {c["value"] for c in choice_set["choices"]}:
            chosen.append((choice_set["key"], value))
    return chosen


def submit(pdf_path, printer, dry_run, dry_run_dir, copies=1, page_ranges=None,
           duplex=False, fit_to_page=False, title=None, color="color", paper=None, quality=None):
    """Send a PDF to a CUPS printer. Returns the job id.

    printer must already be resolved (see resolve_printer).
    color: "color" or "mono" (black & white).
    paper / quality: values from the printer's paper and quality choices (others are ignored).
    """
    copies = max(1, min(int(copies), 99))
    if dry_run:
        dry_run_dir = Path(dry_run_dir)
        dry_run_dir.mkdir(parents=True, exist_ok=True)
        job_id = f"dry-run-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        shutil.copy(pdf_path, dry_run_dir / f"{job_id}.pdf")
        return job_id

    caps = printer_capabilities(printer)
    mono = color == "mono" and caps["color"]
    duplex = duplex and caps["duplex"]
    args = build_lp_args(pdf_path, printer, copies, page_ranges, duplex, fit_to_page,
                         title, mono, caps["mono_option"] if mono else None,
                         driver_options(caps, paper, quality))
    result = _run(args, timeout=60)
    invalidate()
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or "lp failed")
    match = re.search(r"request id is (\S+)", result.stdout)
    return match.group(1) if match else result.stdout.strip()

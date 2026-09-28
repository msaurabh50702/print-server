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
import subprocess
import threading
import time
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
     "state": "idle", "ok": True, "is_default": True, "duplex": True, "color": False},
    {"name": "Test_Colour_Inkjet", "description": "Test colour inkjet (dry run)",
     "state": "idle", "ok": True, "is_default": False, "duplex": False, "color": True},
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


def parse_lpoptions(text):
    """Work out duplex/colour support from `lpoptions -p NAME -l` output.

    Returns {"duplex": bool, "color": bool, "mono_option": (key, value) | None}.
    """
    options = {}
    for line in text.splitlines():
        match = re.match(r"^([^/:\s]+)(?:/[^:]*)?:\s*(.*)$", line)
        if match:
            key, values = match.groups()
            options[key] = [v.lstrip("*") for v in values.split()]

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
    return {"duplex": duplex, "color": color, "mono_option": mono_option}


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
        caps = {"duplex": True, "color": False, "mono_option": None}
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
        printer.update(is_default=printer["name"] == default, duplex=caps["duplex"],
                       color=caps["color"],
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
    if not printer["ok"]:
        return "Offline", False
    return (f"Printing ({printer['queued']})" if printer["queued"] else "Ready"), True


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
                  fit_to_page=False, title=None, mono=False, mono_option=None):
    args = ["lp", "-d", printer, "-n", str(copies), "-o", "media=A4"]
    args += ["-o", "sides=two-sided-long-edge" if duplex else "sides=one-sided"]
    if copies > 1:
        args += ["-o", "collate=true"]
    if page_ranges:
        args += ["-P", page_ranges]
    if fit_to_page:
        args += ["-o", "fit-to-page"]
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


def submit(pdf_path, printer, dry_run, dry_run_dir, copies=1, page_ranges=None,
           duplex=False, fit_to_page=False, title=None, color="color"):
    """Send a PDF to a CUPS printer. Returns the job id.

    printer must already be resolved (see resolve_printer).
    color: "color" or "mono" (black & white).
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
                         title, mono, caps["mono_option"] if mono else None)
    result = _run(args, timeout=60)
    invalidate()
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or "lp failed")
    match = re.search(r"request id is (\S+)", result.stdout)
    return match.group(1) if match else result.stdout.strip()

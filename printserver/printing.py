"""Thin wrapper around the CUPS command line tools (lp, lpstat, lpq, cancel)."""
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

# CUPS queue names: letters, digits, _ - . (no spaces or slashes).
PRINTER_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,127}$")
CUPS_JOB_ID_RE = re.compile(r"^([A-Za-z0-9_.\-]{1,127})-(\d{1,9})$")

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


def default_printer():
    result = _run(["lpstat", "-d"])
    match = re.search(r"system default destination:\s*(\S+)", result.stdout)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Printers and their capabilities
# ---------------------------------------------------------------------------

def parse_lpstat_printers(text):
    """Parse `lpstat -l -p` output into [{name, description, state, ok}]."""
    printers, current = [], None
    for line in text.splitlines():
        match = re.match(r"^printer (\S+) (.*)$", line)
        if match:
            name, rest = match.groups()
            if "disabled" in rest:
                state = "disabled"
            elif "now printing" in rest:
                state = "printing"
            else:
                state = "idle"
            current = {"name": name, "description": name, "state": state, "ok": state != "disabled"}
            printers.append(current)
            continue
        match = re.match(r"^\s+Description:\s*(.+)$", line)
        if match and current:
            current["description"] = match.group(1).strip()
    return printers


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


def printer_capabilities(name):
    """Capabilities of one printer. Falls back to 'duplex allowed, mono only'."""
    result = _run(["lpoptions", "-p", name, "-l"])
    if result.returncode != 0 or not result.stdout.strip():
        return {"duplex": True, "color": False, "mono_option": None}
    return parse_lpoptions(result.stdout)


def list_printers(dry_run=False):
    """All CUPS printers with state and capabilities. Never raises."""
    if dry_run:
        return [dict(p) for p in DRY_RUN_PRINTERS]
    try:
        printers = parse_lpstat_printers(_run(["lpstat", "-l", "-p"]).stdout)
        default = default_printer()
        for printer in printers:
            printer["is_default"] = printer["name"] == default
            caps = printer_capabilities(printer["name"])
            printer["duplex"], printer["color"] = caps["duplex"], caps["color"]
    except PrintError:
        return []
    return printers


def resolve_printer(requested, configured, dry_run=False):
    """Pick the printer for a job and check it exists.

    requested: printer chosen in the UI (may be empty).
    configured: PRINTER_NAME from the settings (may be empty).
    """
    printers = list_printers(dry_run)
    names = [p["name"] for p in printers]
    if requested:
        if not PRINTER_NAME_RE.match(requested) or requested not in names:
            raise PrintError(f"Printer '{requested}' is not available")
        return requested
    if configured and configured in names:
        return configured
    for printer in printers:
        if printer.get("is_default"):
            return printer["name"]
    if names:
        return names[0]
    raise PrintError("No printer configured. Run scripts/setup-printer.sh on the Pi.")


def printer_status(requested, configured, dry_run=False):
    """Status of the chosen (or default) printer. Never raises."""
    printers = list_printers(dry_run)
    if not printers:
        return {"ok": False, "printer": None, "printers": 0,
                "message": "No printer configured. Run scripts/setup-printer.sh on the Pi."}
    try:
        name = resolve_printer(requested, configured, dry_run)
    except PrintError:
        name = resolve_printer("", configured, dry_run)
    printer = next(p for p in printers if p["name"] == name)
    queued = 0 if dry_run else len(active_jobs([name]))
    return {"ok": printer["ok"], "printer": name, "description": printer["description"],
            "state": printer["state"], "printers": len(printers), "queued_jobs": queued,
            "message": f"{printer['description']}: {printer['state']}"}


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

def parse_lpq(text, printer):
    """Parse `lpq -P PRINTER` output into a list of active/waiting jobs."""
    jobs = []
    for line in text.splitlines():
        match = re.match(r"^(\S+)\s+(\S+)\s+(\d+)\s+(.*?)\s+(\d+) bytes\s*$", line)
        if not match or match.group(1) == "Rank":
            continue
        rank, owner, number, title, size = match.groups()
        jobs.append({"id": f"{printer}-{number}", "printer": printer, "rank": rank,
                     "state": "printing" if rank == "active" else "waiting",
                     "owner": owner, "title": title.strip(), "size": int(size)})
    return jobs


def active_jobs(printer_names):
    """Jobs still printing or waiting on the given printers."""
    jobs = []
    for name in printer_names:
        try:
            result = _run(["lpq", "-P", name])
        except PrintError:
            continue
        if result.returncode == 0:
            jobs.extend(parse_lpq(result.stdout, name))
    return jobs


def cancel_job(job_id, printer_names):
    """Cancel an active job. Only jobs currently in the queue can be cancelled."""
    if not CUPS_JOB_ID_RE.match(job_id or ""):
        raise PrintError("Invalid job")
    if job_id not in {job["id"] for job in active_jobs(printer_names)}:
        raise PrintError("This job has already finished or was cancelled")
    result = _run(["cancel", job_id])
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
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or "lp failed")
    match = re.search(r"request id is (\S+)", result.stdout)
    return match.group(1) if match else result.stdout.strip()

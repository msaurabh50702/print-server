"""Thin wrapper around the CUPS command line tools (lp / lpstat)."""
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path


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


def resolve_printer(name):
    return name or default_printer()


def printer_status(name):
    """Return a dict describing the printer state, never raises."""
    try:
        printer = resolve_printer(name)
        if not printer:
            return {"ok": False, "printer": None,
                    "message": "No printer configured. Run scripts/setup-printer.sh on the Pi."}
        result = _run(["lpstat", "-p", printer])
        if result.returncode != 0:
            return {"ok": False, "printer": printer,
                    "message": result.stderr.strip() or f"Printer '{printer}' not found"}
        text = result.stdout.strip()
        disabled = "disabled" in text
        jobs = _run(["lpstat", "-o", printer]).stdout.strip().splitlines()
        return {"ok": not disabled, "printer": printer, "message": text, "queued_jobs": len(jobs)}
    except PrintError as exc:
        return {"ok": False, "printer": name or None, "message": str(exc)}


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


def submit(pdf_path, printer_name, dry_run, dry_run_dir, copies=1,
           page_ranges=None, duplex=False, fit_to_page=False, title=None):
    """Send a PDF to CUPS. Returns a job identifier string."""
    copies = max(1, min(int(copies), 99))
    if dry_run:
        dry_run_dir = Path(dry_run_dir)
        dry_run_dir.mkdir(parents=True, exist_ok=True)
        job_id = f"dry-run-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        shutil.copy(pdf_path, dry_run_dir / f"{job_id}.pdf")
        return job_id

    printer = resolve_printer(printer_name)
    if not printer:
        raise PrintError("No printer configured. Run scripts/setup-printer.sh on the Pi.")

    args = ["lp", "-d", printer, "-n", str(copies), "-o", "media=A4"]
    args += ["-o", "sides=two-sided-long-edge" if duplex else "sides=one-sided"]
    if copies > 1:
        args += ["-o", "collate=true"]
    if page_ranges:
        args += ["-P", page_ranges]
    if fit_to_page:
        args += ["-o", "fit-to-page"]
    if title:
        args += ["-t", title[:100]]
    args.append(str(pdf_path))

    result = _run(args, timeout=60)
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or "lp failed")
    match = re.search(r"request id is (\S+)", result.stdout)
    return match.group(1) if match else result.stdout.strip()

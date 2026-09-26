"""Runtime configuration, read from environment variables."""
import os
from pathlib import Path


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    # CUPS queue name of the printer. Empty = use the CUPS default printer.
    PRINTER_NAME = os.environ.get("PRINTER_NAME", "")

    # When true, nothing is sent to CUPS; generated PDFs are copied to
    # DRY_RUN_DIR instead. Useful for testing without a printer.
    DRY_RUN = _bool("DRY_RUN")

    DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/pi-print-server"))
    DRY_RUN_DIR = Path(os.environ.get("DRY_RUN_DIR", str(DATA_DIR / "dry-run")))

    # Uploaded files and generated PDFs older than this are deleted.
    JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "3600"))

    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024

    # Page geometry used for photo sheets (millimetres).
    PAGE_MARGIN_MM = float(os.environ.get("PAGE_MARGIN_MM", "5"))
    CELL_GAP_MM = float(os.environ.get("CELL_GAP_MM", "3"))
    PHOTO_DPI = int(os.environ.get("PHOTO_DPI", "300"))

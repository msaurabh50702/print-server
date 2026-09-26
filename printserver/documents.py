"""Converting uploaded documents to PDF and rendering page previews."""
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
OFFICE_EXTS = {".doc", ".docx", ".odt", ".rtf", ".txt", ".xls", ".xlsx", ".ods",
               ".csv", ".ppt", ".pptx", ".odp"}
ALLOWED_EXTS = {".pdf"} | IMAGE_EXTS | OFFICE_EXTS


class ConversionError(Exception):
    pass


def to_pdf(source, workdir):
    """Convert an uploaded file to PDF. Returns the PDF path."""
    source, workdir = Path(source), Path(workdir)
    ext = source.suffix.lower()
    target = workdir / "document.pdf"

    if ext == ".pdf":
        if source != target:
            shutil.move(source, target)
    elif ext in IMAGE_EXTS:
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.save(target, "PDF", resolution=150)
    elif ext in OFFICE_EXTS:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise ConversionError("LibreOffice is needed for this file type "
                                  "(sudo apt install libreoffice-core libreoffice-writer)")
        # A private profile dir avoids clashing with other soffice instances.
        profile = (workdir / "lo-profile").resolve().as_uri()
        result = subprocess.run(
            [soffice, f"-env:UserInstallation={profile}", "--headless",
             "--convert-to", "pdf", "--outdir", str(workdir), str(source)],
            capture_output=True, text=True, timeout=180)
        converted = workdir / (source.stem + ".pdf")
        if result.returncode != 0 or not converted.exists():
            if "could not be loaded" in result.stderr:
                raise ConversionError("LibreOffice cannot open this file. Is the right component installed? "
                                      "(sudo apt install libreoffice-writer libreoffice-calc libreoffice-impress)")
            raise ConversionError("Could not convert document: " + (result.stderr.strip() or "unknown error"))
        converted.rename(target)
    else:
        raise ConversionError(f"Unsupported file type '{ext}'")

    try:
        page_count(target)
    except Exception as exc:
        raise ConversionError(f"Not a readable PDF: {exc}")
    return target


def page_count(pdf_path):
    return len(PdfReader(str(pdf_path)).pages)


def render_page(pdf_path, page_number, out_path, width=900):
    """Render one page (1-based) to PNG using poppler's pdftoppm."""
    out_path = Path(out_path)
    if out_path.exists():
        return out_path
    if not shutil.which("pdftoppm"):
        raise ConversionError("pdftoppm is missing (sudo apt install poppler-utils)")
    prefix = out_path.with_suffix("")
    subprocess.run(
        ["pdftoppm", "-png", "-singlefile", "-f", str(page_number), "-l", str(page_number),
         "-scale-to-x", str(width), "-scale-to-y", "-1", str(pdf_path), str(prefix)],
        check=True, capture_output=True, timeout=60)
    return out_path

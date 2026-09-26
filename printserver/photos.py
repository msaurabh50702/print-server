"""Photo sheet layouts and A4 page composition."""
from PIL import Image, ImageOps

A4_MM = (210.0, 297.0)
MM_PER_INCH = 25.4

# id -> (columns, rows, label). Pages are A4 portrait.
LAYOUTS = {
    "1": (1, 1, "1 photo (full page)"),
    "2": (1, 2, "2 photos"),
    "4": (2, 2, "4 photos"),
    "6": (2, 3, "6 photos"),
    "8": (2, 4, "8 photos"),
    "9": (3, 3, "9 photos"),
    "12": (3, 4, "12 photos"),
    "16": (4, 4, "16 photos (stamp size)"),
}


def cell_geometry(layout_id, margin_mm, gap_mm):
    """Return (cols, rows, cell_w_mm, cell_h_mm) for a layout."""
    cols, rows, _ = LAYOUTS[layout_id]
    cell_w = (A4_MM[0] - 2 * margin_mm - (cols - 1) * gap_mm) / cols
    cell_h = (A4_MM[1] - 2 * margin_mm - (rows - 1) * gap_mm) / rows
    return cols, rows, cell_w, cell_h


def layouts_for_client(margin_mm, gap_mm):
    result = []
    for layout_id, (cols, rows, label) in LAYOUTS.items():
        _, _, cell_w, cell_h = cell_geometry(layout_id, margin_mm, gap_mm)
        result.append({
            "id": layout_id, "cols": cols, "rows": rows, "label": label,
            "cell_w_mm": round(cell_w, 1), "cell_h_mm": round(cell_h, 1),
        })
    return result


def _mm_to_px(mm, dpi):
    return int(round(mm / MM_PER_INCH * dpi))


def compose_sheet(layout_id, images, margin_mm, gap_mm, dpi=300, fit="fill"):
    """Compose photos onto an A4 page.

    images: dict {cell_index: file-like or path}. Missing cells stay blank.
    fit: "fill" crops the photo to cover the whole cell, "fit" letterboxes it.
    Returns a PIL RGB image of the full page.
    """
    if layout_id not in LAYOUTS:
        raise ValueError(f"Unknown layout '{layout_id}'")
    cols, rows, cell_w_mm, cell_h_mm = cell_geometry(layout_id, margin_mm, gap_mm)
    page = Image.new("RGB", (_mm_to_px(A4_MM[0], dpi), _mm_to_px(A4_MM[1], dpi)), "white")
    cell_w, cell_h = _mm_to_px(cell_w_mm, dpi), _mm_to_px(cell_h_mm, dpi)

    for index, source in images.items():
        if not 0 <= index < cols * rows:
            raise ValueError(f"Cell {index} does not exist in layout '{layout_id}'")
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            if fit == "fit":
                img = ImageOps.contain(img, (cell_w, cell_h), Image.LANCZOS)
            else:
                img = ImageOps.fit(img, (cell_w, cell_h), Image.LANCZOS)
        col, row = index % cols, index // cols
        x = _mm_to_px(margin_mm + col * (cell_w_mm + gap_mm), dpi) + (cell_w - img.width) // 2
        y = _mm_to_px(margin_mm + row * (cell_h_mm + gap_mm), dpi) + (cell_h - img.height) // 2
        page.paste(img, (x, y))
    return page


def save_pdf(page, path, dpi=300):
    page.save(path, "PDF", resolution=dpi)

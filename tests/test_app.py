import glob
import io
import shutil

import pytest
from PIL import Image
from pypdf import PdfReader

from printserver import create_app
from printserver.photos import ID_CARD_MM, LAYOUTS, compose_id_card, compose_sheet
from printserver.printing import parse_page_ranges


@pytest.fixture
def app(tmp_path):
    return create_app({"DATA_DIR": tmp_path, "DRY_RUN": True,
                       "DRY_RUN_DIR": tmp_path / "out", "TESTING": True})


@pytest.fixture
def client(app):
    return app.test_client()


def jpeg(color="red", size=(400, 300)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def test_pages_render(client):
    for url in ("/", "/photos", "/id-card", "/document"):
        assert client.get(url).status_code == 200


def test_status_dry_run(client):
    assert client.get("/api/status").get_json()["dry_run"] is True


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_compose_every_layout(layout):
    cols, rows, _ = LAYOUTS[layout]
    images = {i: jpeg() for i in range(cols * rows)}
    page = compose_sheet(layout, images, 5, 3, dpi=100)
    assert page.size == (827, 1169)  # A4 at 100 dpi
    # Top-left cell is filled, page margin stays white.
    assert page.getpixel((40, 40))[0] > 200 and page.getpixel((40, 40))[1] < 60
    assert page.getpixel((5, 5)) == (255, 255, 255)


def test_compose_rejects_bad_cell():
    with pytest.raises(ValueError):
        compose_sheet("4", {4: jpeg()}, 5, 3, dpi=100)


def test_photo_print_dry_run(client, app):
    resp = client.post("/api/photos/print", data={
        "layout": "4", "fit": "fill", "copies": "2",
        "cell0": (jpeg(), "a.jpg"), "cell3": (jpeg("blue"), "b.jpg"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    job = resp.get_json()["job"]
    out = app.config["DRY_RUN_DIR"] / f"{job}.pdf"
    assert len(PdfReader(str(out)).pages) == 1


def test_photo_pdf_download(client):
    resp = client.post("/api/photos/pdf", data={"layout": "2", "cell1": (jpeg(), "a.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    assert resp.data.startswith(b"%PDF")


def test_photo_print_requires_photo(client):
    resp = client.post("/api/photos/print", data={"layout": "4"})
    assert resp.status_code == 400
    assert "photo" in resp.get_json()["error"]


def test_photo_print_bad_layout(client):
    resp = client.post("/api/photos/print", data={"layout": "7", "cell0": (jpeg(), "a.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_document_image_upload_and_print(client, app):
    resp = client.post("/api/documents", data={"file": (jpeg(), "scan.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    doc = resp.get_json()
    assert doc["pages"] == 1

    assert client.get(f"/api/documents/{doc['id']}/pdf").data.startswith(b"%PDF")
    if shutil.which("pdftoppm"):
        png = client.get(f"/api/documents/{doc['id']}/page/1.png")
        assert png.status_code == 200 and png.data.startswith(b"\x89PNG")
    assert client.get(f"/api/documents/{doc['id']}/page/2.png").status_code == 404

    resp = client.post(f"/api/documents/{doc['id']}/print",
                       json={"copies": 1, "pages": "1", "duplex": True})
    assert resp.status_code == 200
    bad = client.post(f"/api/documents/{doc['id']}/print", json={"pages": "2-5"})
    assert bad.status_code == 400


def test_document_rejects_unknown_type(client):
    resp = client.post("/api/documents", data={"file": (io.BytesIO(b"x"), "evil.exe")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_document_bad_id(client):
    assert client.get("/api/documents/../../etc/pdf").status_code == 404
    assert client.get("/api/documents/" + "0" * 32 + "/pdf").status_code == 404


@pytest.mark.skipif(not glob.glob("/usr/lib/libreoffice/program/libsw*.so"),
                    reason="LibreOffice Writer not installed")
def test_text_document_converts(client):
    resp = client.post("/api/documents", data={"file": (io.BytesIO(b"hello printer"), "note.txt")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["pages"] == 1


def test_parse_page_ranges():
    assert parse_page_ranges("", 5) is None
    assert parse_page_ranges(" 1-3, 5 ", 5) == "1-3,5"
    for bad in ("0", "3-2", "6", "a", "1-"):
        with pytest.raises(ValueError):
            parse_page_ranges(bad, 5)


def test_id_card_real_size_layout():
    dpi = 100
    page = compose_id_card(jpeg("red", (856, 540)), jpeg("blue", (856, 540)), dpi=dpi, outline=False)
    assert page.size == (827, 1169)
    card_w = round(ID_CARD_MM[0] / 25.4 * dpi)
    card_h = round(ID_CARD_MM[1] / 25.4 * dpi)
    # Front is centred in the top half, back in the bottom half, both at real size.
    row = [page.getpixel((x, 1169 // 4)) for x in range(page.width)]
    red = [x for x, px in enumerate(row) if px[0] > 200 and px[2] < 80]
    assert abs(len(red) - card_w) <= 1
    assert abs(red[0] - (page.width - card_w) // 2) <= 1
    col = [page.getpixel((page.width // 2, y)) for y in range(page.height)]
    assert abs(sum(1 for px in col if px[0] > 200 and px[2] < 80) - card_h) <= 1
    assert abs(sum(1 for px in col if px[2] > 200 and px[0] < 80) - card_h) <= 1


def test_id_card_front_only_and_outline():
    page = compose_id_card(jpeg("red", (856, 540)), None, dpi=100, outline=True)
    # Bottom half stays blank when there is no back side.
    assert page.crop((0, 600, 827, 1169)).getextrema() == ((255, 255), (255, 255), (255, 255))


def test_id_card_print_dry_run(client, app):
    resp = client.post("/api/id-card/print", data={
        "front": (jpeg(), "front.jpg"), "back": (jpeg("blue"), "back.jpg"), "copies": "2",
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    out = app.config["DRY_RUN_DIR"] / f"{resp.get_json()['job']}.pdf"
    page = PdfReader(str(out)).pages[0]
    # The PDF page must be exactly A4 so the card prints at real size.
    assert round(float(page.mediabox.width) / 72 * 25.4) == 210
    assert round(float(page.mediabox.height) / 72 * 25.4) == 297


def test_id_card_pdf_and_missing_photo(client):
    resp = client.post("/api/id-card/pdf", data={"back": (jpeg(), "b.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200 and resp.data.startswith(b"%PDF")
    resp = client.post("/api/id-card/print", data={})
    assert resp.status_code == 400

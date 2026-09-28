import glob
import io
import shutil
import subprocess

import pytest
from PIL import Image
from pypdf import PdfReader

from printserver import create_app
from printserver.photos import (ID_CARD_MM, LAYOUTS, PASSPORT_SIZES, compose_id_card, compose_passport,
                                compose_sheet, passport_grid)
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
    for url in ("/", "/photos", "/passport", "/id-card", "/document"):
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


@pytest.mark.parametrize("size_id", list(PASSPORT_SIZES))
def test_passport_real_size_and_count(size_id):
    dpi = 100
    w_mm, h_mm = PASSPORT_SIZES[size_id][:2]
    cols, rows = passport_grid(size_id, 5, 3)
    assert cols >= 3 and rows >= 4
    page, total = compose_passport(jpeg("red", (350, 450)), size_id, None, 5, 3, dpi=dpi, outline=False)
    assert total == cols * rows
    # First photo starts at the top-left margin, exactly the requested size.
    margin_px = round(5 / 25.4 * dpi)
    row = [page.getpixel((x, margin_px + 3)) for x in range(margin_px, margin_px + 400)]
    width = next(i for i, px in enumerate(row) if px == (255, 255, 255))
    assert abs(width - round(w_mm / 25.4 * dpi)) <= 1
    col = [page.getpixel((margin_px + 3, y)) for y in range(margin_px, margin_px + 400)]
    height = next(i for i, px in enumerate(col) if px == (255, 255, 255))
    assert abs(height - round(h_mm / 25.4 * dpi)) <= 1


def test_passport_count_is_clamped():
    _, total = compose_passport(jpeg(), "51x51", 999, 5, 3, dpi=50)
    cols, rows = passport_grid("51x51", 5, 3)
    assert total == cols * rows
    _, total = compose_passport(jpeg(), "35x45", 8, 5, 3, dpi=50)
    assert total == 8


def test_passport_print_dry_run(client, app):
    resp = client.post("/api/passport/print", data={
        "photo": (jpeg(), "me.jpg"), "size": "35x45", "count": "8", "copies": "1",
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    out = app.config["DRY_RUN_DIR"] / f"{resp.get_json()['job']}.pdf"
    page = PdfReader(str(out)).pages[0]
    assert round(float(page.mediabox.width) / 72 * 25.4) == 210


def test_passport_errors(client):
    assert client.post("/api/passport/print", data={"size": "35x45"}).status_code == 400
    bad = client.post("/api/passport/pdf", data={"photo": (jpeg(), "a.jpg"), "size": "1x1"},
                      content_type="multipart/form-data")
    assert bad.status_code == 400
    full = client.post("/api/passport/pdf", data={"photo": (jpeg(), "a.jpg"), "size": "20x25", "count": "full"},
                       content_type="multipart/form-data")
    assert full.status_code == 200 and full.data.startswith(b"%PDF")


def upload_image_doc(client, name="scan.jpg", color="red"):
    resp = client.post("/api/documents", data={"file": (jpeg(color), name)},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def test_batch_print_sends_one_job_per_document(client, app):
    ids = [upload_image_doc(client, f"doc{i}.jpg") for i in range(3)]
    resp = client.post("/api/documents/print", json={
        "documents": [{"id": ids[2]}, {"id": ids[0], "pages": "1"}, {"id": ids[1], "pages": ""}],
        "copies": 2, "duplex": True,
    })
    assert resp.status_code == 200, resp.get_json()
    jobs = resp.get_json()["jobs"]
    assert len(jobs) == 3 and len(set(jobs)) == 3
    for job in jobs:
        assert (app.config["DRY_RUN_DIR"] / f"{job}.pdf").exists()


def test_batch_print_validates_everything_first(client, app):
    good, bad = upload_image_doc(client), upload_image_doc(client, "two.jpg")
    resp = client.post("/api/documents/print", json={
        "documents": [{"id": good}, {"id": bad, "pages": "2-4"}]})
    assert resp.status_code == 400
    assert "two.jpg" in resp.get_json()["error"]
    # Nothing was printed because one document had an invalid page range.
    out = app.config["DRY_RUN_DIR"]
    assert not out.exists() or not list(out.iterdir())


def test_batch_print_rejects_bad_input(client):
    assert client.post("/api/documents/print", json={}).status_code == 400
    assert client.post("/api/documents/print", json={"documents": ["x"]}).status_code == 400
    expired = client.post("/api/documents/print", json={"documents": [{"id": "0" * 32}]})
    assert expired.status_code == 400 and "expired" in expired.get_json()["error"]
    traversal = client.post("/api/documents/print", json={"documents": [{"id": "../../etc"}]})
    assert traversal.status_code == 400
    too_many = client.post("/api/documents/print", json={"documents": [{"id": "0" * 32}] * 21})
    assert too_many.status_code == 400


# ---------------------------------------------------------------------------
# Multiple printers, colour and queue
# ---------------------------------------------------------------------------
from printserver import printing  # noqa: E402

LPSTAT = """printer Canon_MF4820d is idle.  enabled since Mon 28 Sep 2026
\tForm mounted:
\tDescription: Canon MF4820d
printer HP_DeskJet_3835 now printing HP_DeskJet_3835-7.  enabled since Mon 28 Sep 2026
\tDescription: HP DeskJet 3835
printer Old_Printer disabled since Sun 27 Sep 2026 -
\tPaused
"""

HP_OPTIONS = """PageSize/Media Size: Letter *A4 Legal
ColorModel/Output Mode: *RGB CMYGray KGray
OutputMode/Print Quality: Draft *Normal Best
"""

CANON_OPTIONS = """PageSize/Page Size: *A4 Letter
Duplex/Duplex Printing: *None DuplexNoTumble DuplexTumble
CNTonerSaving/Toner Save: *False True
"""

LPQ = """HP_DeskJet_3835 is ready and printing
Rank    Owner   Job     File(s)                         Total Size
active  pi      7       Passport photos                 812345 bytes
1st     pi      8       invoice (final).pdf             45000 bytes
"""


def test_parse_lpstat_printers():
    printers = printing.parse_lpstat_printers(LPSTAT)
    assert [p["name"] for p in printers] == ["Canon_MF4820d", "HP_DeskJet_3835", "Old_Printer"]
    assert printers[0]["description"] == "Canon MF4820d" and printers[0]["state"] == "idle"
    assert printers[1]["state"] == "printing" and printers[1]["ok"]
    assert printers[2]["state"] == "disabled" and not printers[2]["ok"]


def test_parse_lpoptions_colour_and_duplex():
    hp = printing.parse_lpoptions(HP_OPTIONS)
    assert hp == {"duplex": False, "color": True, "mono_option": ("ColorModel", "KGray")}
    canon = printing.parse_lpoptions(CANON_OPTIONS)
    assert canon == {"duplex": True, "color": False, "mono_option": None}


def test_parse_lpq():
    jobs = printing.parse_lpq(LPQ, "HP_DeskJet_3835")
    assert [j["id"] for j in jobs] == ["HP_DeskJet_3835-7", "HP_DeskJet_3835-8"]
    assert jobs[0]["state"] == "printing" and jobs[0]["title"] == "Passport photos"
    assert jobs[1]["state"] == "waiting" and jobs[1]["title"] == "invoice (final).pdf"


def test_build_lp_args_black_and_white():
    args = printing.build_lp_args("/x.pdf", "HP_DeskJet_3835", copies=2, mono=True,
                                  mono_option=("ColorModel", "KGray"), title="Doc")
    assert args[:3] == ["lp", "-d", "HP_DeskJet_3835"]
    assert "print-color-mode=monochrome" in args and "ColorModel=KGray" in args
    assert "collate=true" in args and args[-1] == "/x.pdf"


class FakeCups:
    """Stands in for the CUPS command line tools."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout=20):
        self.calls.append(args)
        out, code = "", 0
        if args[:2] == ["lpstat", "-l"]:
            out = LPSTAT
        elif args[:2] == ["lpstat", "-d"]:
            out = "system default destination: Canon_MF4820d"
        elif args[0] == "lpoptions":
            out = HP_OPTIONS if args[2] == "HP_DeskJet_3835" else CANON_OPTIONS
        elif args[0] == "lpq":
            out = LPQ if args[2] == "HP_DeskJet_3835" else "no entries"
        elif args[0] == "lp":
            out = f"request id is {args[2]}-9 (1 file(s))"
        return subprocess.CompletedProcess(args, code, out, "")


@pytest.fixture
def cups(monkeypatch):
    fake = FakeCups()
    monkeypatch.setattr(printing, "_run", fake)
    return fake


@pytest.fixture
def live_client(tmp_path, cups):
    """App that talks to the fake CUPS instead of dry-run mode."""
    return create_app({"DATA_DIR": tmp_path, "DRY_RUN": False, "TESTING": True}).test_client()


def test_printers_api_lists_capabilities(live_client):
    data = live_client.get("/api/printers").get_json()
    assert data["default"] == "Canon_MF4820d"
    by_name = {p["name"]: p for p in data["printers"]}
    assert by_name["HP_DeskJet_3835"]["color"] and not by_name["HP_DeskJet_3835"]["duplex"]
    assert by_name["Canon_MF4820d"]["duplex"] and not by_name["Canon_MF4820d"]["color"]
    assert by_name["Canon_MF4820d"]["is_default"]


def test_print_goes_to_chosen_printer_in_black_and_white(live_client, cups):
    resp = live_client.post("/api/photos/print", data={
        "layout": "1", "cell0": (jpeg(), "a.jpg"), "printer": "HP_DeskJet_3835", "color": "mono",
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["job"] == "HP_DeskJet_3835-9"
    lp = next(c for c in cups.calls if c[0] == "lp")
    assert lp[2] == "HP_DeskJet_3835" and "ColorModel=KGray" in lp


def test_duplex_dropped_and_mono_ignored_where_unsupported(live_client, cups):
    doc = upload_image_doc(live_client)
    resp = live_client.post("/api/documents/print", json={
        "documents": [{"id": doc}], "printer": "HP_DeskJet_3835", "duplex": True})
    assert resp.status_code == 200
    lp = next(c for c in cups.calls if c[0] == "lp")
    assert "sides=one-sided" in lp  # the DeskJet can't print two-sided
    cups.calls.clear()
    live_client.post("/api/documents/print", json={
        "documents": [{"id": doc}], "printer": "Canon_MF4820d", "duplex": True, "color": "mono"})
    lp = next(c for c in cups.calls if c[0] == "lp")
    assert "sides=two-sided-long-edge" in lp and "print-color-mode=monochrome" not in lp


def test_default_printer_used_when_none_chosen(live_client, cups):
    live_client.post("/api/id-card/print", data={"front": (jpeg(), "f.jpg")},
                     content_type="multipart/form-data")
    assert next(c for c in cups.calls if c[0] == "lp")[2] == "Canon_MF4820d"


def test_unknown_printer_rejected_before_printing(live_client, cups):
    for bad in ("Nope", "-o evil", "../x"):
        resp = live_client.post("/api/passport/print", data={
            "photo": (jpeg(), "p.jpg"), "size": "35x45", "printer": bad,
        }, content_type="multipart/form-data")
        assert resp.status_code == 400
    assert not any(c[0] == "lp" for c in cups.calls)


def test_queue_shows_active_and_recent(live_client, cups):
    live_client.post("/api/photos/print", data={"layout": "1", "cell0": (jpeg(), "a.jpg")},
                     content_type="multipart/form-data")
    data = live_client.get("/api/queue").get_json()
    assert {j["id"] for j in data["active"]} == {"HP_DeskJet_3835-7", "HP_DeskJet_3835-8"}
    # The photo job went to the Canon, which has an empty queue, so it's done.
    assert data["recent"][0]["title"] == "Photos" and data["recent"][0]["state"] == "done"


def test_cancel_only_active_jobs(live_client, cups):
    assert live_client.post("/api/queue/HP_DeskJet_3835-7/cancel").status_code == 200
    assert ["cancel", "HP_DeskJet_3835-7"] in cups.calls
    for bad in ("HP_DeskJet_3835-99", "x;rm -rf", "-a"):
        assert live_client.post(f"/api/queue/{bad}/cancel").status_code in (400, 404)


def test_status_for_chosen_printer(live_client):
    data = live_client.get("/api/status?printer=HP_DeskJet_3835").get_json()
    assert data["printer"] == "HP_DeskJet_3835" and data["queued_jobs"] == 2
    assert data["printers"] == 3


def test_dry_run_printers_and_queue(client):
    names = [p["name"] for p in client.get("/api/printers").get_json()["printers"]]
    assert names == ["Test_Mono_Laser", "Test_Colour_Inkjet"]
    client.post("/api/photos/print", data={"layout": "1", "cell0": (jpeg(), "a.jpg"),
                                           "printer": "Test_Colour_Inkjet"},
                content_type="multipart/form-data")
    recent = client.get("/api/queue").get_json()["recent"]
    assert recent[0]["printer"] == "Test_Colour_Inkjet"
    assert client.get("/queue").status_code == 200

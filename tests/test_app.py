import glob
import io
import os
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
    page = compose_sheet(layout, images, 5, 3, dpi=100, fit="fill")
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
    page = compose_id_card(jpeg("red", (856, 540)), jpeg("blue", (856, 540)), dpi=dpi, outline=False, fit="fill")
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
    page, total = compose_passport(jpeg("red", (350, 450)), size_id, None, 5, 3, dpi=dpi, outline=False,
                                   fit="fill")
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

LPSTAT_JOBS = """HP_DeskJet_3835-7       pi              812345   Mon 28 Sep 2026 06:50:01 PM IST
HP_DeskJet_3835-8       pi               45000   Mon 28 Sep 2026 06:51:12 PM IST
"""


def test_parse_lpstat_printers():
    printers = printing.parse_lpstat_printers(LPSTAT)
    assert [p["name"] for p in printers] == ["Canon_MF4820d", "HP_DeskJet_3835", "Old_Printer"]
    assert printers[0]["description"] == "Canon MF4820d" and printers[0]["state"] == "idle"
    assert printers[1]["state"] == "printing" and printers[1]["ok"]
    assert printers[2]["state"] == "disabled" and not printers[2]["ok"]


def test_parse_lpoptions_colour_and_duplex():
    hp = printing.parse_lpoptions(HP_OPTIONS)
    assert (hp["duplex"], hp["color"], hp["mono_option"]) == (False, True, ("ColorModel", "KGray"))
    assert hp["quality"]["key"] == "OutputMode"
    assert [c["label"] for c in hp["quality"]["choices"]] == ["Draft (saves ink)", "Normal", "Best"]
    canon = printing.parse_lpoptions(CANON_OPTIONS)
    assert (canon["duplex"], canon["color"], canon["mono_option"]) == (True, False, None)
    assert canon["paper"] is None
    assert canon["quality"]["key"] == "CNTonerSaving" and canon["quality"]["default"] == "False"


def test_parse_lpstat_jobs():
    jobs = printing.parse_lpstat_jobs(LPSTAT_JOBS + "garbage line\n")
    assert [j["id"] for j in jobs] == ["HP_DeskJet_3835-7", "HP_DeskJet_3835-8"]
    assert jobs[0]["printer"] == "HP_DeskJet_3835" and jobs[0]["size"] == 812345


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
        elif args[:2] == ["lpstat", "-o"]:
            out = LPSTAT_JOBS
        elif args[0] == "lp":
            out = f"request id is {args[2]}-9 (1 file(s))"
        return subprocess.CompletedProcess(args, code, out, "")


HP_IPP = {"marker-names": ["Black ink", "Tri-color ink"], "marker-levels": [8, 60],
          "marker-colors": ["#000000", "#00FFFF#FF00FF#FFFF00"], "marker-low-levels": [10, 10],
          "printer-state-reasons": ["media-empty-error"]}


@pytest.fixture
def cups(monkeypatch, tmp_path):
    fake = FakeCups()
    monkeypatch.setattr(printing, "_run", fake)
    monkeypatch.setattr(printing, "ipp_printer_attributes",
                        lambda name: HP_IPP if name == "HP_DeskJet_3835" else {})
    monkeypatch.setattr(printing, "PPD_DIR", tmp_path / "ppd")
    monkeypatch.setattr(printing, "USB_SYSFS", tmp_path / "no-usb")
    printing.invalidate()
    printing._caps_cache.clear()
    yield fake
    printing.invalidate()
    printing._caps_cache.clear()


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
    states = {j["id"]: j["state"] for j in data["active"]}
    assert states == {"HP_DeskJet_3835-7": "printing", "HP_DeskJet_3835-8": "waiting"}
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


# Real `lpoptions -l` output from an HP DeskJet 3835 (driverless / IPP Everywhere).
HP_DESKJET_3835_OPTIONS = """PageSize/Media Size: 100x150mm 100x150mm.Borderless 4x6 *A4 A4.Borderless A5 Letter Custom.WIDTHxHEIGHT
MediaType/Media Type: *Stationery PhotographicGlossy Com.hp.specialtyGlossy Com.hp.specialtyMatte
ColorModel/Print Color Mode: *RGB Gray DeviceGray DeviceRGB AdobeRGB
OutputBin/Output Tray: *FaceUp
cupsPrintQuality/Print Quality: Draft *Normal High
print-content-optimize/Print Optimization: *auto photo graphics text text-and-graphics
print-rendering-intent/Print Rendering Intent: *auto perceptual
print-scaling/Print Scaling: *auto auto-fit fill fit none
"""


def test_hp_deskjet_3835_capabilities():
    caps = printing.parse_lpoptions(HP_DESKJET_3835_OPTIONS)
    assert (caps["duplex"], caps["color"], caps["mono_option"]) == (False, True, ("ColorModel", "Gray"))
    assert caps["paper"]["key"] == "MediaType" and caps["paper"]["default"] == "Stationery"
    assert [c["label"] for c in caps["paper"]["choices"]] == [
        "Plain paper", "Photo glossy", "HP glossy", "HP matte"]
    assert caps["quality"] == {"key": "cupsPrintQuality", "default": "Normal", "choices": [
        {"value": "Draft", "label": "Draft (saves ink)"}, {"value": "Normal", "label": "Normal"},
        {"value": "High", "label": "Best"}]}


def test_printer_state_is_cached_between_requests(live_client, cups):
    for _ in range(5):
        live_client.get("/api/printers")
        live_client.get("/api/queue")
    # One snapshot: lpstat -l -p, -d, -o once; lpoptions once per printer.
    assert sum(1 for c in cups.calls if c[:2] == ["lpstat", "-l"]) == 1
    assert sum(1 for c in cups.calls if c[0] == "lpoptions") == 3


def test_snapshot_refreshes_after_ttl_and_after_printing(live_client, cups, monkeypatch):
    live_client.get("/api/printers")
    monkeypatch.setattr(printing, "SNAPSHOT_TTL", 0)
    live_client.get("/api/printers")
    assert sum(1 for c in cups.calls if c[:2] == ["lpstat", "-l"]) == 2
    # Capabilities are not re-read when the state refreshes.
    assert sum(1 for c in cups.calls if c[0] == "lpoptions") == 3


def test_capabilities_reread_when_printer_reconfigured(cups, tmp_path):
    ppd_dir = tmp_path / "ppd"
    ppd_dir.mkdir()
    ppd = ppd_dir / "HP_DeskJet_3835.ppd"
    ppd.write_text("v1")
    printing.printer_capabilities("HP_DeskJet_3835")
    printing.printer_capabilities("HP_DeskJet_3835")
    assert sum(1 for c in cups.calls if c[0] == "lpoptions") == 1
    os.utime(ppd, (1, 1))  # e.g. lpadmin rewrote the PPD
    printing.printer_capabilities("HP_DeskJet_3835")
    assert sum(1 for c in cups.calls if c[0] == "lpoptions") == 2


def test_pages_embed_printer_state(live_client):
    html = live_client.get("/document").get_data(as_text=True)
    assert "window.BOOT = " in html and "HP_DeskJet_3835" in html
    assert "Checking" not in html and ">Ready<" in html
    queue_html = live_client.get("/queue").get_data(as_text=True)
    assert "window.INITIAL_QUEUE = " in queue_html and "HP_DeskJet_3835-7" in queue_html


# ---------------------------------------------------------------------------
# Installable app (PWA)
# ---------------------------------------------------------------------------
import json  # noqa: E402


def test_manifest_is_installable(client):
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200 and resp.mimetype == "application/manifest+json"
    manifest = json.loads(resp.data)
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    sizes = {i["sizes"] for i in manifest["icons"] if i["type"] == "image/png"}
    assert {"192x192", "512x512"} <= sizes
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
    assert 'href="/manifest.webmanifest"' in client.get("/").get_data(as_text=True)


def test_service_worker_served_from_root_uncached(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200 and "javascript" in resp.mimetype
    assert "no-cache" in resp.headers["Cache-Control"]
    assert client.get("/offline").status_code == 200


def test_install_page_and_ca_download(client, app, tmp_path):
    app.config["CA_CERT_PATH"] = tmp_path / "missing.crt"
    page = client.get("/install", base_url="http://printer.local").get_data(as_text=True)
    assert "enable-https.sh" in page and "/ca.crt" not in page
    assert client.get("/ca.crt").status_code == 404

    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n")
    app.config["CA_CERT_PATH"] = ca
    page = client.get("/install", base_url="http://printer.local").get_data(as_text=True)
    assert 'href="/ca.crt"' in page and 'href="https://printer.local/install"' in page
    resp = client.get("/ca.crt")
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "application/x-x509-ca-cert"
    assert resp.data.startswith(b"-----BEGIN CERTIFICATE-----")


# ---------------------------------------------------------------------------
# Share to Printer
# ---------------------------------------------------------------------------

def pdf_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (100, 140), "white").save(buf, "PDF")
    buf.seek(0)
    return buf


def test_manifest_registers_share_target(client):
    target = json.loads(client.get("/manifest.webmanifest").data)["share_target"]
    assert target["action"] == "/share" and target["method"] == "POST"
    assert target["enctype"] == "multipart/form-data"
    accept = target["params"]["files"][0]["accept"]
    assert "image/*" in accept and "application/pdf" in accept


def test_share_documents_go_straight_to_document_page(client):
    resp = client.post("/share", data={"files": [(pdf_bytes(), "bill.pdf"), (pdf_bytes(), "form.pdf")]},
                       content_type="multipart/form-data")
    assert resp.status_code == 303 and "/document?share=" in resp.headers["Location"]
    share_id = resp.headers["Location"].split("share=")[1]
    items = client.get(f"/api/shares/{share_id}").get_json()["items"]
    assert [i["name"] for i in items] == ["bill.pdf", "form.pdf"]
    assert not any(i["image"] for i in items)


def test_share_photos_offer_choices(client):
    resp = client.post("/share", data={"files": [(jpeg(), "IMG_1.jpg"), (jpeg("blue"), "IMG_2.jpg")]},
                       content_type="multipart/form-data")
    assert resp.status_code == 303 and "/shared/" in resp.headers["Location"]
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Photo page" in page and "ID card copy" in page
    assert "Passport photos" not in page  # only offered for a single photo
    share_id = resp.headers["Location"].rsplit("/", 1)[1].split("?")[0]
    item = client.get(f"/api/shares/{share_id}").get_json()["items"][0]
    original = client.get(f"/api/documents/{item['id']}/original")
    assert original.status_code == 200 and original.data[:2] == b"\xff\xd8"  # the JPEG itself


def test_share_file_without_extension_uses_mime_type(client):
    data = {"files": [(jpeg(), "shared image", "image/jpeg")]}
    resp = client.post("/share", data=data, content_type="multipart/form-data")
    share_id = resp.headers["Location"].rsplit("/", 1)[1].split("?")[0]
    items = client.get(f"/api/shares/{share_id}").get_json()["items"]
    assert len(items) == 1 and items[0]["image"]


def test_share_unsupported_or_empty(client):
    resp = client.post("/share", data={"files": [(io.BytesIO(b"x"), "app.apk")]},
                       content_type="multipart/form-data")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "couldn&#39;t be read" in page or "couldn't be read" in page
    resp = client.post("/share", data={"text": "hello"}, content_type="multipart/form-data")
    assert "Nothing to print" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_share_ids_are_validated(client):
    assert client.get("/api/shares/../../etc").status_code == 404
    assert client.get("/shared/" + "0" * 32).status_code == 404
    doc = upload_image_doc(client)
    pdf_doc = client.post("/api/documents", data={"file": (pdf_bytes(), "a.pdf")},
                          content_type="multipart/form-data").get_json()["id"]
    assert client.get(f"/api/documents/{doc}/original").status_code == 200
    assert client.get(f"/api/documents/{pdf_doc}/original").status_code == 404


# ---------------------------------------------------------------------------
# Paper type, quality, scaling
# ---------------------------------------------------------------------------

def test_driver_options_only_allow_printer_choices():
    caps = printing.parse_lpoptions(HP_DESKJET_3835_OPTIONS)
    assert printing.driver_options(caps, "PhotographicGlossy", "High") == [
        ("MediaType", "PhotographicGlossy"), ("cupsPrintQuality", "High")]
    assert printing.driver_options(caps, "evil -o x", "Ultra") == []
    assert printing.driver_options({"paper": None, "quality": None}, "PhotographicGlossy", "High") == []


def test_lp_args_scaling_and_extra_options():
    fit = printing.build_lp_args("/x.pdf", "P", fit_to_page=True)
    assert "fit-to-page" in fit and "print-scaling=fit" in fit
    actual = printing.build_lp_args("/x.pdf", "P", fit_to_page=False,
                                    extra_options=[("MediaType", "PhotographicGlossy")])
    assert "print-scaling=none" in actual and "fit-to-page" not in actual
    assert "MediaType=PhotographicGlossy" in actual


def test_paper_and_quality_reach_the_printer(live_client, cups):
    resp = live_client.post("/api/photos/print", data={
        "layout": "1", "cell0": (jpeg(), "a.jpg"), "printer": "HP_DeskJet_3835",
        "paper": "Com.hp.specialtyGlossy", "quality": "Best"}, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    lp = next(c for c in cups.calls if c[0] == "lp")
    # HP_OPTIONS (hpcups) has no MediaType; quality "Best" is a valid OutputMode.
    assert "OutputMode=Best" in lp and not any(a.startswith("MediaType=") for a in lp)
    assert "print-scaling=none" in lp  # photo sheets print exactly as composed


def test_document_actual_size(live_client, cups):
    doc = upload_image_doc(live_client)
    live_client.post("/api/documents/print", json={"documents": [{"id": doc}], "fit_to_page": False})
    lp = next(c for c in cups.calls if c[0] == "lp")
    assert "print-scaling=none" in lp and "fit-to-page" not in lp
    cups.calls.clear()
    live_client.post("/api/documents/print", json={"documents": [{"id": doc}]})
    assert "fit-to-page" in next(c for c in cups.calls if c[0] == "lp")


# ---------------------------------------------------------------------------
# Ink / toner levels and alerts
# ---------------------------------------------------------------------------

def ipp_response(attrs):
    """Build a minimal IPP Get-Printer-Attributes response."""
    import struct
    body = struct.pack(">BBHI", 2, 0, 0, 1) + b"\x01" + b"\x04"
    for name, values in attrs.items():
        for i, value in enumerate(values):
            key = name.encode() if i == 0 else b""
            if isinstance(value, int):
                tag, raw = 0x21, struct.pack(">i", value)
            else:
                tag, raw = 0x41 if name == "marker-names" else 0x44, value.encode()
            body += struct.pack(">BH", tag, len(key)) + key + struct.pack(">H", len(raw)) + raw
    return body + b"\x03"


def test_ipp_request_and_response_roundtrip():
    request = printing.build_ipp_request("HP_DeskJet_3835")
    assert request[:4] == b"\x02\x00\x00\x0b"
    assert b"ipp://localhost/printers/HP_DeskJet_3835" in request and request.endswith(b"\x03")
    parsed = printing.parse_ipp_response(ipp_response(HP_IPP))
    assert parsed["marker-levels"] == [8, 60]
    assert parsed["printer-state-reasons"] == ["media-empty-error"]
    assert printing.parse_ipp_response(b"\x02\x00\x04\x00\x00\x00\x00\x01\x03") == {}  # error status


def test_supplies_and_alerts():
    supplies, alerts = printing.supplies_and_alerts(HP_IPP)
    assert supplies[0] == {"name": "Black ink", "level": 8, "color": "#000000", "low": True}
    assert supplies[1]["color"] == "multi" and not supplies[1]["low"]
    assert alerts[0] == {"text": "Out of paper", "severity": "error"}
    assert {"text": "Black ink low", "severity": "warning"} in alerts
    assert printing.supplies_and_alerts({}) == ([], [])
    _, alerts = printing.supplies_and_alerts({"printer-state-reasons": ["offline-report", "none"]})
    assert alerts == [{"text": "Printer not connected", "severity": "error"}]


def test_alerts_shown_in_status_and_printer_list(live_client):
    data = live_client.get("/api/printers").get_json()
    hp = next(p for p in data["printers"] if p["name"] == "HP_DeskJet_3835")
    assert hp["alerts"][0]["text"] == "Out of paper" and hp["supplies"][0]["level"] == 8
    status = live_client.get("/api/status?printer=HP_DeskJet_3835").get_json()
    assert status["printer"] == "HP_DeskJet_3835"
    label, ok = printing.status_label(hp)
    assert (label, ok) == ("Out of paper", False)


# ---------------------------------------------------------------------------
# HEIC photos
# ---------------------------------------------------------------------------

def heic_bytes(color="green", size=(120, 80)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="HEIF")
    buf.seek(0)
    return buf


import importlib.util  # noqa: E402

needs_heif = pytest.mark.skipif(importlib.util.find_spec("pillow_heif") is None,
                                reason="pillow-heif not installed")


@needs_heif
def test_heic_document_upload(client):
    resp = client.post("/api/documents", data={"file": (heic_bytes(), "IMG_0001.HEIC")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["pages"] == 1 and resp.get_json()["image"]


@needs_heif
def test_heic_converted_to_jpeg_for_editors(client):
    resp = client.post("/api/images/jpeg", data={"file": (heic_bytes(size=(300, 200)), "a.heic")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200 and resp.mimetype == "image/jpeg"
    assert Image.open(io.BytesIO(resp.data)).size == (300, 200)
    bad = client.post("/api/images/jpeg", data={"file": (io.BytesIO(b"nope"), "x.heic")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400


@needs_heif
def test_heic_shared_without_extension(client):
    resp = client.post("/share", data={"files": [(heic_bytes(), "shared", "image/heic")]},
                       content_type="multipart/form-data")
    assert "/shared/" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Free size
# ---------------------------------------------------------------------------
from printserver.photos import compose_free  # noqa: E402


def test_compose_free_places_photo_at_exact_size():
    dpi = 100
    page = compose_free([(jpeg("red", (400, 300)), 20, 30, 100, 75)], dpi=dpi)
    px = lambda mm: round(mm / 25.4 * dpi)  # noqa: E731
    assert page.getpixel((px(20) + 2, px(30) + 2))[0] > 200            # inside the photo
    assert page.getpixel((px(20) - 3, px(30) + 5)) == (255, 255, 255)   # just left of it
    assert page.getpixel((px(120) + 3, px(60))) == (255, 255, 255)      # just right of it
    assert page.getpixel((px(119) - 1, px(104) - 1))[0] > 200            # bottom-right corner


def test_compose_free_clips_and_validates():
    page = compose_free([(jpeg("blue"), -50, -50, 100, 100)], dpi=50)  # partly off the page
    assert page.getpixel((2, 2))[2] > 200
    for bad in [(10, 10, 0, 10), (10, 10, 10, float("nan")), (10, 10, 1e9, 10)]:
        with pytest.raises(ValueError):
            compose_free([(jpeg(), *bad)], dpi=50)
    with pytest.raises(ValueError):
        compose_free([], dpi=50)


def test_free_size_print_and_pdf(client, app):
    layout = json.dumps([{"x": 10, "y": 10, "w": 90, "h": 60}, {"x": 110, "y": 10, "w": 90, "h": 60}])
    resp = client.post("/api/free-size/print", data={
        "items": layout, "item0": (jpeg(), "a.jpg"), "item1": (jpeg("blue"), "b.jpg"),
        "printer": "Test_Colour_Inkjet", "paper": "PhotographicGlossy"},
        content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    out = app.config["DRY_RUN_DIR"] / f"{resp.get_json()['job']}.pdf"
    assert round(float(PdfReader(str(out)).pages[0].mediabox.width) / 72 * 25.4) == 210
    pdf = client.post("/api/free-size/pdf", data={"items": layout, "item0": (jpeg(), "a.jpg"),
                                                  "item1": (jpeg(), "b.jpg")},
                      content_type="multipart/form-data")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    missing = client.post("/api/free-size/print", data={"items": layout, "item0": (jpeg(), "a.jpg")},
                          content_type="multipart/form-data")
    assert missing.status_code == 400
    assert client.post("/api/free-size/print", data={"items": "not json"}).status_code == 400
    assert client.get("/free-size").status_code == 200


# ---------------------------------------------------------------------------
# Photo fit: Whole photo (default) vs Fill box
# ---------------------------------------------------------------------------

def test_id_card_whole_photo_vs_fill():
    dpi = 50
    tall = lambda: jpeg("red", (200, 400))  # noqa: E731  (much taller than the card box)
    card_w = round(ID_CARD_MM[0] / 25.4 * dpi)
    x0 = (round(210 / 25.4 * dpi) - card_w) // 2
    half = round(297 / 25.4 * dpi) // 2
    y_mid = (half - round(ID_CARD_MM[1] / 25.4 * dpi)) // 2 + round(ID_CARD_MM[1] / 25.4 * dpi) // 2
    whole = compose_id_card(tall(), None, dpi=dpi, outline=False)           # default = whole photo
    assert whole.getpixel((x0 + 3, y_mid)) == (255, 255, 255)               # white border at the side
    assert whole.getpixel((x0 + card_w // 2, y_mid))[0] > 200               # photo in the middle
    fill = compose_id_card(tall(), None, dpi=dpi, outline=False, fit="fill")
    assert fill.getpixel((x0 + 3, y_mid))[0] > 200                          # box filled edge to edge


def test_passport_whole_photo_vs_fill():
    wide = lambda: jpeg("red", (800, 200))  # noqa: E731
    whole, _ = compose_passport(wide(), "35x45", 1, 5, 3, dpi=100)
    fill, _ = compose_passport(wide(), "35x45", 1, 5, 3, dpi=100, fit="fill")
    top_inside = (round(5 / 25.4 * 100) + 10, round(5 / 25.4 * 100) + 3)
    assert whole.getpixel(top_inside) == (255, 255, 255)
    assert fill.getpixel(top_inside)[0] > 200


def test_photo_pages_default_to_whole_photo(client):
    for url in ("/photos", "/id-card", "/passport"):
        html = client.get(url).get_data(as_text=True)
        assert 'value="fit" checked><span>Whole photo' in html, url


def _usb(root, name, **files):
    dev = root / name
    dev.mkdir(parents=True)
    for key, value in files.items():
        (dev / key).write_text(value + "\n")


def test_usb_connected(tmp_path):
    _usb(tmp_path, "1-1.2", manufacturer="HP", product="DeskJet 3830 series", serial="CN12345")
    _usb(tmp_path, "1-1.3", manufacturer="Canon", product="MF4800 Series", serial="ABC999")
    _usb(tmp_path, "usb1", manufacturer="Linux", product="DWC OTG Controller")
    devices = printing.usb_devices(tmp_path)
    assert len(devices) == 3

    hp = "ipp://HP%20DeskJet%203830%20series%20(USB)._ipp._tcp.local/"
    canon = "usb://Canon/MF4800%20Series?serial=ABC999"
    assert printing.usb_connected(hp, devices) is True
    assert printing.usb_connected(canon, devices) is True
    assert printing.usb_connected("usb://Canon/MF4800%20Series", devices) is True
    assert printing.usb_connected("usb://Canon/MF4800%20Series?serial=OTHER", devices) is False
    # Switched off: gone from the USB device list.
    assert printing.usb_connected(hp, [d for d in devices if d["manufacturer"] != "HP"]) is False
    # Network printers and unknown platforms can't be judged.
    assert printing.usb_connected("ipp://192.168.0.50/ipp/print", devices) is None
    assert printing.usb_connected(hp, None) is None
    assert printing.usb_devices(tmp_path / "missing") is None


def test_usb_connected_ipp_usb_port(monkeypatch):
    opened = []
    monkeypatch.setattr(printing, "_port_open", lambda host, port: opened.append(port) or port == 60000)
    assert printing.usb_connected("ipp://localhost:60000/ipp/print", None) is True
    assert printing.usb_connected("ipp://127.0.0.1:60001/ipp/print", []) is False
    assert printing.usb_connected("ipp://localhost:631/printers/x", []) is None
    assert opened == [60000, 60001]


def test_parse_lpstat_devices():
    text = ("device for Canon_MF4820d: usb://Canon/MF4800%20Series?serial=ABC999\n"
            "device for HP_DeskJet_3835: ipp://HP%20DeskJet%203830%20series%20(USB)._ipp._tcp.local/\n")
    assert printing.parse_lpstat_devices(text) == {
        "Canon_MF4820d": "usb://Canon/MF4800%20Series?serial=ABC999",
        "HP_DeskJet_3835": "ipp://HP%20DeskJet%203830%20series%20(USB)._ipp._tcp.local/"}


def test_switched_off_status():
    printer = {"ok": True, "queued": 0, "alerts": [], "connected": False}
    assert printing.status_label(printer) == ("Switched off", False)
    assert printing.status_label(dict(printer, connected=None)) == ("Ready", True)

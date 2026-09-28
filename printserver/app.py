"""Flask web app: photo sheets and document printing over the home network."""
import re
import shutil
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from . import documents, photos, printing
from .history import JobHistory
from .config import Config

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_BATCH = 20


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    data_dir = Path(app.config["DATA_DIR"])
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    history = JobHistory(data_dir / "history.json")

    def cleanup_old_jobs():
        cutoff = time.time() - app.config["JOB_TTL_SECONDS"]
        for job in jobs_dir.iterdir():
            try:
                if job.stat().st_mtime < cutoff:
                    shutil.rmtree(job, ignore_errors=True)
            except FileNotFoundError:
                pass

    def new_job_dir():
        cleanup_old_jobs()
        job_id = uuid.uuid4().hex
        path = jobs_dir / job_id
        path.mkdir()
        return job_id, path

    def job_dir(job_id):
        if not JOB_ID_RE.match(job_id):
            abort(404)
        path = jobs_dir / job_id
        if not (path / "document.pdf").exists():
            abort(404)
        return path

    def overview():
        """Printers, their status and the default printer, for the UI.

        Embedded in every page (so nothing has to load after the page opens)
        and served by /api/printers for the periodic refresh.
        """
        dry_run = app.config["DRY_RUN"]
        state = printing.snapshot(dry_run)
        try:
            default = printing.resolve_printer("", app.config["PRINTER_NAME"], dry_run, state)
        except printing.PrintError:
            default = None
        default_info = next((p for p in state["printers"] if p["name"] == default), None)
        label, ok = printing.status_label(default_info, dry_run)
        return {"printers": state["printers"], "default": default, "dry_run": dry_run,
                "status": {"text": label, "ok": ok}}

    def queue_data():
        dry_run = app.config["DRY_RUN"]
        active = printing.active_jobs(dry_run)
        by_id = {job["id"]: job for job in active}
        recent = []
        for entry in history.recent():
            job = by_id.get(entry["id"])
            if job:
                job.update(title=entry["title"], time=entry["time"], copies=entry.get("copies", 1))
                continue
            state = "cancelled" if entry.get("cancelled") else "done"
            recent.append({**entry, "state": state})
        return {"active": active, "recent": recent[:20]}

    @app.context_processor
    def inject_boot():
        return {"boot": overview()}

    def print_target(options):
        """(printer, colour mode) chosen in the UI, validated against CUPS."""
        printer = printing.resolve_printer(
            str(options.get("printer") or "").strip(), app.config["PRINTER_NAME"],
            app.config["DRY_RUN"])
        color = "mono" if options.get("color") == "mono" else "color"
        return printer, color

    def submit(pdf_path, target, title, copies=1, **kwargs):
        printer, color = target
        job = printing.submit(
            pdf_path, printer, app.config["DRY_RUN"], app.config["DRY_RUN_DIR"],
            copies=copies, title=title, color=color, **kwargs)
        history.add(job, printer, title, copies)
        return job

    def int_arg(value, default, low, high):
        try:
            return max(low, min(int(value), high))
        except (TypeError, ValueError):
            return default

    @app.errorhandler(printing.PrintError)
    @app.errorhandler(documents.ConversionError)
    def handle_known_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify(error="File is too large"), 413

    # ---------- pages ----------

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/photos")
    def photos_page():
        layouts = photos.layouts_for_client(app.config["PAGE_MARGIN_MM"], app.config["CELL_GAP_MM"])
        sheet = {"margin_mm": app.config["PAGE_MARGIN_MM"], "gap_mm": app.config["CELL_GAP_MM"],
                 "page_w_mm": photos.A4_MM[0]}
        return render_template("photos.html", layouts=layouts, sheet=sheet)

    @app.get("/id-card")
    def id_card_page():
        card = {"w_mm": photos.ID_CARD_MM[0], "h_mm": photos.ID_CARD_MM[1]}
        return render_template("idcard.html", card=card)

    @app.get("/passport")
    def passport_page():
        sizes = photos.passport_sizes_for_client(app.config["PAGE_MARGIN_MM"], app.config["CELL_GAP_MM"])
        sheet = {"margin_mm": app.config["PAGE_MARGIN_MM"], "gap_mm": app.config["CELL_GAP_MM"],
                 "page_w_mm": photos.A4_MM[0], "page_h_mm": photos.A4_MM[1]}
        return render_template("passport.html", sizes=sizes, counts=photos.PASSPORT_COUNTS, sheet=sheet)

    @app.get("/queue")
    def queue_page():
        return render_template("queue.html", initial_queue=queue_data())

    @app.get("/document")
    def document_page():
        return render_template("document.html", accept=",".join(sorted(documents.ALLOWED_EXTS)),
                               max_batch=MAX_BATCH)

    # ---------- API ----------

    @app.get("/api/status")
    def status():
        info = printing.printer_status(
            request.args.get("printer", ""), app.config["PRINTER_NAME"], app.config["DRY_RUN"])
        if app.config["DRY_RUN"]:
            info.update(dry_run=True, message="Dry-run mode: jobs are saved, not printed")
        return jsonify(info)

    @app.get("/api/printers")
    def printers_list():
        return jsonify(overview())

    @app.get("/api/queue")
    def queue_list():
        return jsonify(queue_data())

    @app.post("/api/queue/<job_id>/cancel")
    def queue_cancel(job_id):
        if app.config["DRY_RUN"]:
            raise printing.PrintError("Test mode: jobs are saved as PDFs, nothing to cancel")
        printing.cancel_job(job_id)
        history.mark_cancelled(job_id)
        return jsonify(ok=True)

    def compose_photo_request():
        layout = request.form.get("layout", "")
        if layout not in photos.LAYOUTS:
            raise printing.PrintError("Unknown layout")
        images = {}
        for key, file in request.files.items():
            match = re.fullmatch(r"cell(\d+)", key)
            if match and file.filename is not None:
                images[int(match.group(1))] = file.stream
        if not images:
            raise printing.PrintError("Add at least one photo first")
        fit = "fit" if request.form.get("fit") == "fit" else "fill"
        try:
            page = photos.compose_sheet(
                layout, images, app.config["PAGE_MARGIN_MM"], app.config["CELL_GAP_MM"],
                app.config["PHOTO_DPI"], fit)
        except (ValueError, OSError) as exc:
            raise printing.PrintError(f"Could not read photo: {exc}")
        _, path = new_job_dir()
        pdf_path = path / "document.pdf"
        photos.save_pdf(page, pdf_path, app.config["PHOTO_DPI"])
        return pdf_path

    @app.post("/api/photos/pdf")
    def photos_pdf():
        pdf_path = compose_photo_request()
        return send_file(pdf_path, mimetype="application/pdf",
                         as_attachment=True, download_name="photos.pdf")

    @app.post("/api/photos/print")
    def photos_print():
        target = print_target(request.form)  # check the printer before the slow work
        pdf_path = compose_photo_request()
        copies = int_arg(request.form.get("copies"), 1, 1, 99)
        job = submit(pdf_path, target, "Photos", copies=copies)
        return jsonify(job=job)

    def compose_id_card_request():
        front, back = request.files.get("front"), request.files.get("back")
        if not front and not back:
            raise printing.PrintError("Add a photo of the card first")
        outline = request.form.get("outline", "1") != "0"
        try:
            page = photos.compose_id_card(
                front.stream if front else None, back.stream if back else None,
                app.config["PHOTO_DPI"], outline)
        except (ValueError, OSError) as exc:
            raise printing.PrintError(f"Could not read photo: {exc}")
        _, path = new_job_dir()
        pdf_path = path / "document.pdf"
        photos.save_pdf(page, pdf_path, app.config["PHOTO_DPI"])
        return pdf_path

    @app.post("/api/id-card/pdf")
    def id_card_pdf():
        return send_file(compose_id_card_request(), mimetype="application/pdf",
                         as_attachment=True, download_name="id-card.pdf")

    @app.post("/api/id-card/print")
    def id_card_print():
        target = print_target(request.form)
        pdf_path = compose_id_card_request()
        copies = int_arg(request.form.get("copies"), 1, 1, 99)
        return jsonify(job=submit(pdf_path, target, "ID card copy", copies=copies))

    def compose_passport_request():
        photo = request.files.get("photo")
        if not photo:
            raise printing.PrintError("Add a photo first")
        size = request.form.get("size", "")
        if size not in photos.PASSPORT_SIZES:
            raise printing.PrintError("Unknown photo size")
        count_arg = request.form.get("count", "full")
        count = None if count_arg == "full" else int_arg(count_arg, 1, 1, 999)
        outline = request.form.get("outline", "1") != "0"
        try:
            page, _ = photos.compose_passport(
                photo.stream, size, count, app.config["PAGE_MARGIN_MM"], app.config["CELL_GAP_MM"],
                app.config["PHOTO_DPI"], outline)
        except (ValueError, OSError) as exc:
            raise printing.PrintError(f"Could not read photo: {exc}")
        _, path = new_job_dir()
        pdf_path = path / "document.pdf"
        photos.save_pdf(page, pdf_path, app.config["PHOTO_DPI"])
        return pdf_path

    @app.post("/api/passport/pdf")
    def passport_pdf():
        return send_file(compose_passport_request(), mimetype="application/pdf",
                         as_attachment=True, download_name="passport-photos.pdf")

    @app.post("/api/passport/print")
    def passport_print():
        target = print_target(request.form)
        pdf_path = compose_passport_request()
        copies = int_arg(request.form.get("copies"), 1, 1, 99)
        return jsonify(job=submit(pdf_path, target, "Passport photos", copies=copies))

    @app.post("/api/documents")
    def upload_document():
        file = request.files.get("file")
        if not file or not file.filename:
            raise printing.PrintError("Choose a file to upload")
        name = secure_filename(file.filename) or "upload"
        ext = Path(name).suffix.lower()
        if ext not in documents.ALLOWED_EXTS:
            raise documents.ConversionError(f"Unsupported file type '{ext or name}'")
        job_id, path = new_job_dir()
        source = path / f"source{ext}"
        file.save(source)
        try:
            pdf = documents.to_pdf(source, path)
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
        (path / "name.txt").write_text(file.filename[:200])
        return jsonify(id=job_id, name=file.filename, pages=documents.page_count(pdf))

    @app.get("/api/documents/<job_id>/page/<int:page>.png")
    def document_page_png(job_id, page):
        path = job_dir(job_id)
        pdf = path / "document.pdf"
        if not 1 <= page <= documents.page_count(pdf):
            abort(404)
        png = documents.render_page(pdf, page, path / f"page-{page}.png")
        return send_file(png, mimetype="image/png", max_age=3600)

    @app.get("/api/documents/<job_id>/pdf")
    def document_pdf(job_id):
        return send_file(job_dir(job_id) / "document.pdf", mimetype="application/pdf")

    def document_name(path):
        name_file = path / "name.txt"
        return name_file.read_text() if name_file.exists() else "Document"

    def prepare_document(job_id, pages):
        """Validate one document and its page range. Returns (pdf, ranges, name)."""
        if not JOB_ID_RE.match(str(job_id)) or not (jobs_dir / job_id / "document.pdf").exists():
            raise printing.PrintError("A document has expired. Please upload it again.")
        path = jobs_dir / job_id
        pdf = path / "document.pdf"
        name = document_name(path)
        try:
            ranges = printing.parse_page_ranges(pages, documents.page_count(pdf))
        except ValueError as exc:
            raise printing.PrintError(f"{name}: {exc}")
        return pdf, ranges, name

    def print_documents(items, options):
        # Validate everything before sending anything, so a bad page range
        # doesn't leave half the batch printed.
        target = print_target(options)
        prepared = [prepare_document(item.get("id"), item.get("pages")) for item in items]
        copies = int_arg(options.get("copies"), 1, 1, 99)
        duplex = bool(options.get("duplex"))
        fit = bool(options.get("fit_to_page", True))
        # One CUPS job per document keeps them in order and stops two-sided
        # printing from putting one document on the back of another.
        return [submit(pdf, target, name, copies=copies, page_ranges=ranges,
                       duplex=duplex, fit_to_page=fit)
                for pdf, ranges, name in prepared]

    @app.post("/api/documents/<job_id>/print")
    def document_print(job_id):
        job_dir(job_id)
        options = request.get_json(silent=True) or {}
        jobs = print_documents([{"id": job_id, "pages": options.get("pages")}], options)
        return jsonify(job=jobs[0])

    @app.post("/api/documents/print")
    def documents_print_batch():
        options = request.get_json(silent=True) or {}
        items = options.get("documents")
        if not isinstance(items, list) or not items:
            raise printing.PrintError("Add at least one document")
        if len(items) > MAX_BATCH:
            raise printing.PrintError(f"You can print up to {MAX_BATCH} documents at once")
        if not all(isinstance(item, dict) for item in items):
            raise printing.PrintError("Invalid document list")
        return jsonify(jobs=print_documents(items, options))

    return app

"""Flask web app: photo sheets and document printing over the home network."""
import functools
import hashlib
import json
import mimetypes
import re
import shutil
import time
import uuid
from pathlib import Path

from flask import (Flask, abort, jsonify, make_response, redirect, render_template, request,
                   send_file, send_from_directory, url_for)
from werkzeug.utils import secure_filename

from . import documents, photos, printing, system
from .history import JobHistory
from .sentjobs import SentJobs
from .config import Config

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
JOB_KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
# Pages the installed app keeps on the phone so it opens without the server.
OFFLINE_PAGES = ["/", "/photos", "/free-size", "/passport", "/id-card", "/document", "/queue", "/offline"]
for _type, _ext in (("image/heic", ".heic"), ("image/heif", ".heif"), ("image/webp", ".webp")):
    mimetypes.add_type(_type, _ext)
MAX_BATCH = 20
RECENT_JOBS = 8   # finished jobs listed on the queue page


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    data_dir = Path(app.config["DATA_DIR"])
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    history = JobHistory(data_dir / "history.json")
    sent_jobs = SentJobs(data_dir / "sent-jobs.json")

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
        warn = ok and bool(default_info and default_info.get("alerts")) and not default_info["queued"]
        return {"printers": state["printers"], "default": default, "dry_run": dry_run,
                "generated": time.time(),
                "status": {"text": label, "ok": ok,
                           "state": "warn" if warn and not dry_run else ("ok" if ok else "bad")}}

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
        return {"active": active, "recent": recent[:RECENT_JOBS], "now": time.time()}

    @app.context_processor
    def inject_boot():
        return {"boot": overview()}

    def print_target(options):
        """Printer, colour, paper and quality chosen in the UI, validated against CUPS.

        Paper/quality values are checked against the printer's own choices
        when the job is sent (printing.driver_options).
        """
        printer = printing.resolve_printer(
            str(options.get("printer") or "").strip(), app.config["PRINTER_NAME"],
            app.config["DRY_RUN"])
        return {"printer": printer,
                "color": "mono" if options.get("color") == "mono" else "color",
                "paper": str(options.get("paper") or "")[:100] or None,
                "quality": str(options.get("quality") or "")[:100] or None}

    def submit(pdf_path, target, title, copies=1, **kwargs):
        job = printing.submit(
            pdf_path, target["printer"], app.config["DRY_RUN"], app.config["DRY_RUN_DIR"],
            copies=copies, title=title, color=target["color"], paper=target["paper"],
            quality=target["quality"], **kwargs)
        history.add(job, target["printer"], title, copies)
        return job

    def photo_fit(form):
        """"fill" (trim edges to fill the box) or "fit" (whole photo, the default)."""
        return "fill" if form.get("fit") == "fill" else "fit"

    def int_arg(value, default, low, high):
        try:
            return max(low, min(int(value), high))
        except (TypeError, ValueError):
            return default

    def idempotent(view):
        """Answer a repeated request (same X-Job-Key header) with the first reply.

        Phones retry print jobs that were saved while the server was offline,
        and on weak Wi-Fi the first attempt may have printed already.
        """
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            key = request.headers.get("X-Job-Key", "")
            if not key:
                return view(*args, **kwargs)
            if not JOB_KEY_RE.match(key):
                raise printing.PrintError("Invalid job key")
            state, saved = sent_jobs.claim(key)
            if state == "done":
                return jsonify(saved)
            if state == "busy":
                return jsonify(error="This job is still being sent"), 409
            try:
                response = view(*args, **kwargs)
            except BaseException:
                sent_jobs.release(key)
                raise
            if response.status_code == 200:
                sent_jobs.finish(key, response.get_json())
            else:
                sent_jobs.release(key)
            return response
        return wrapper

    @app.errorhandler(printing.PrintError)
    @app.errorhandler(documents.ConversionError)
    def handle_known_error(exc):
        if request.method == "POST":
            # Enough to tell a bad request from one that arrived damaged
            # (see: journalctl -u print-server).
            app.logger.warning(
                "%s refused: %s (%s bytes, %s; fields %s; files %s)", request.path, exc,
                request.content_length, request.mimetype, sorted(request.form.keys()),
                {k: f.filename for k, f in request.files.items()})
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

    # ---------- installable app (PWA) ----------

    @app.get("/manifest.webmanifest")
    def manifest():
        response = send_from_directory(app.static_folder, "manifest.webmanifest",
                                       mimetype="application/manifest+json")
        response.cache_control.max_age = 3600
        return response

    @functools.lru_cache(maxsize=1)
    def offline_assets():
        """Static files the installed app keeps on the phone, and a version for them.

        The version changes whenever a file changes (e.g. after git pull and a
        restart), so the phone's service worker notices and downloads the new files.
        """
        static = Path(app.static_folder)
        files = sorted(p for p in static.rglob("*")
                       if p.is_file() and p.name != "sw.js" and p.suffix in
                       (".js", ".css", ".png", ".svg", ".webmanifest"))
        digest = hashlib.sha256()
        for folder in (static, Path(app.root_path) / app.template_folder):
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    digest.update(str(path.relative_to(folder)).encode())
                    digest.update(path.read_bytes())
        urls = ["/static/" + p.relative_to(static).as_posix() for p in files]
        return digest.hexdigest()[:12], urls

    @app.get("/sw.js")
    def service_worker():
        # Served from the root so it controls the whole app; never cached so
        # updates to it are picked up straight away.
        version, assets = offline_assets()
        source = (Path(app.static_folder) / "sw.js").read_text()
        source = (source.replace('"__VERSION__"', json.dumps(version))
                  .replace("__PAGES__", json.dumps(OFFLINE_PAGES))
                  .replace("__ASSETS__", json.dumps(assets + ["/manifest.webmanifest"])))
        response = make_response(source)
        response.mimetype = "text/javascript"
        response.cache_control.no_cache = True
        return response

    @app.get("/offline")
    def offline_page():
        return render_template("offline.html")

    @app.get("/install")
    def install_page():
        return render_template("install.html", has_ca=Path(app.config["CA_CERT_PATH"]).is_file(),
                               host=request.host.split(":")[0])

    @app.get("/ca.crt")
    def ca_certificate():
        path = Path(app.config["CA_CERT_PATH"])
        if not path.is_file():
            abort(404)
        response = make_response(path.read_bytes())
        # This type makes Android and iOS offer to install the certificate.
        response.headers["Content-Type"] = "application/x-x509-ca-cert"
        response.headers["Content-Disposition"] = 'attachment; filename="home-printer-ca.crt"'
        return response

    @app.get("/queue")
    def queue_page():
        return render_template("queue.html", initial_queue=queue_data())

    @app.get("/free-size")
    def free_size_page():
        sheet = {"page_w_mm": photos.A4_MM[0], "page_h_mm": photos.A4_MM[1],
                 "margin_mm": app.config["PAGE_MARGIN_MM"], "max_items": photos.FREE_MAX_ITEMS}
        return render_template("freesize.html", sheet=sheet)

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

    @app.get("/api/ping")
    def ping():
        """Cheap check that the server can be reached, before sending a saved job."""
        response = jsonify(ok=True)
        response.cache_control.no_store = True
        return response

    def live(data):
        """Printer and queue state must never be answered from a browser cache."""
        response = jsonify(data)
        response.cache_control.no_store = True
        return response

    @app.get("/api/printers")
    def printers_list():
        return live(overview())

    @app.get("/api/queue")
    def queue_list():
        return live(queue_data())

    @app.post("/api/queue/<job_id>/cancel")
    def queue_cancel(job_id):
        if app.config["DRY_RUN"]:
            raise printing.PrintError("Test mode: jobs are saved as PDFs, nothing to cancel")
        printing.cancel_job(job_id)
        history.mark_cancelled(job_id)
        return jsonify(ok=True)

    @app.post("/api/clock")
    def set_clock():
        """A phone sends its time when the Pi's clock is clearly wrong (no internet time)."""
        data = request.get_json(silent=True) or {}
        since = system.boot_time()
        result = system.set_clock(data.get("now"), app.config["DRY_RUN"])
        if result["adjusted"] and since is not None:
            # Jobs printed since the Pi started were stamped with the wrong time.
            history.shift_since(since, result["offset"])
        return jsonify(result)

    @app.post("/api/system/<action>")
    def system_power(action):
        if action not in system.POWER_ACTIONS:
            abort(404)
        system.power(action, app.config["DRY_RUN"])
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
        fit = photo_fit(request.form)
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
    @idempotent
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
                app.config["PHOTO_DPI"], outline, photo_fit(request.form))
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
    @idempotent
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
                app.config["PHOTO_DPI"], outline, photo_fit(request.form))
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
    @idempotent
    def passport_print():
        target = print_target(request.form)
        pdf_path = compose_passport_request()
        copies = int_arg(request.form.get("copies"), 1, 1, 99)
        return jsonify(job=submit(pdf_path, target, "Passport photos", copies=copies))

    def store_upload(file):
        """Save an uploaded file and convert it to PDF. Returns {id, name, pages, image}."""
        display_name = (file.filename or "").strip()[:200]
        ext = Path(secure_filename(display_name)).suffix.lower()
        if ext not in documents.ALLOWED_EXTS:
            # Files shared from other apps sometimes arrive without an extension.
            guessed = mimetypes.guess_extension(file.mimetype or "") or ""
            ext = {".jpe": ".jpg", ".jpeg": ".jpg"}.get(guessed, guessed)
        if ext not in documents.ALLOWED_EXTS:
            raise documents.ConversionError(f"Unsupported file type '{ext or display_name or file.mimetype}'")
        display_name = display_name or f"Shared file{ext}"
        job_id, path = new_job_dir()
        source = path / f"source{ext}"
        file.save(source)
        try:
            pdf = documents.to_pdf(source, path)
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
        (path / "name.txt").write_text(display_name)
        return {"id": job_id, "name": display_name, "pages": documents.page_count(pdf),
                "image": ext in documents.IMAGE_EXTS}

    def compose_free_request():
        try:
            layout = json.loads(request.form.get("items") or "[]")
            if not isinstance(layout, list):
                raise ValueError
            items = []
            for i, item in enumerate(layout[:photos.FREE_MAX_ITEMS + 1]):
                file = request.files.get(f"item{i}")
                if not file:
                    raise ValueError
                items.append((file.stream, item["x"], item["y"], item["w"], item["h"]))
        except (ValueError, TypeError, KeyError):
            raise printing.PrintError("Invalid page layout")
        try:
            page = photos.compose_free(items, app.config["PHOTO_DPI"])
        except (ValueError, OSError) as exc:
            raise printing.PrintError(str(exc))
        _, path = new_job_dir()
        pdf_path = path / "document.pdf"
        photos.save_pdf(page, pdf_path, app.config["PHOTO_DPI"])
        return pdf_path

    @app.post("/api/free-size/pdf")
    def free_size_pdf():
        return send_file(compose_free_request(), mimetype="application/pdf",
                         as_attachment=True, download_name="free-size.pdf")

    @app.post("/api/free-size/print")
    @idempotent
    def free_size_print():
        target = print_target(request.form)
        pdf_path = compose_free_request()
        copies = int_arg(request.form.get("copies"), 1, 1, 99)
        return jsonify(job=submit(pdf_path, target, "Free size photos", copies=copies))

    @app.post("/api/images/jpeg")
    def image_to_jpeg():
        """Convert a photo the phone's browser can't show (e.g. HEIC) to JPEG."""
        file = request.files.get("file")
        if not file:
            raise printing.PrintError("No image")
        try:
            data = documents.to_jpeg(file.stream)
        except Exception:
            raise documents.ConversionError("This image format isn't supported")
        return app.response_class(data, mimetype="image/jpeg")

    @app.post("/api/documents")
    def upload_document():
        file = request.files.get("file")
        if not file or not file.filename:
            raise printing.PrintError("Choose a file to upload")
        return jsonify(store_upload(file))

    @app.get("/api/documents/<job_id>/original")
    def document_original(job_id):
        """The uploaded image itself (for the photo, passport and ID card editors)."""
        path = job_dir(job_id)
        for source in path.glob("source.*"):
            if source.suffix.lower() in documents.IMAGE_EXTS:
                return send_file(source, max_age=3600)
        abort(404)

    # ---------- Share to Printer (Android share sheet) ----------

    shares_dir = data_dir / "shares"
    shares_dir.mkdir(exist_ok=True)
    SHARE_ID_RE = re.compile(r"^[0-9a-f]{32}$")

    def load_share(share_id):
        if not SHARE_ID_RE.match(share_id):
            abort(404)
        path = shares_dir / f"{share_id}.json"
        if not path.exists():
            abort(404)
        items = json.loads(path.read_text())
        # Drop files that have since been cleaned up.
        return [i for i in items if (jobs_dir / i["id"] / "document.pdf").exists()]

    @app.post("/share")
    def share_target():
        """Files shared from other apps arrive here (see share_target in the manifest)."""
        cutoff = time.time() - app.config["JOB_TTL_SECONDS"]
        for old in shares_dir.glob("*.json"):
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        items, failed = [], []
        for file in request.files.getlist("files")[:MAX_BATCH]:
            try:
                items.append(store_upload(file))
            except (documents.ConversionError, printing.PrintError, OSError) as exc:
                failed.append(f"{file.filename or 'file'}: {exc}")
        share_id = uuid.uuid4().hex
        (shares_dir / f"{share_id}.json").write_text(json.dumps(items))
        if items and not any(i["image"] for i in items) and not failed:
            return redirect(url_for("document_page", share=share_id), code=303)
        return redirect(url_for("shared_page", share_id=share_id,
                                failed=len(failed) or None), code=303)

    @app.get("/shared/<share_id>")
    def shared_page(share_id):
        items = load_share(share_id)
        images = [i for i in items if i["image"]]
        return render_template("shared.html", share_id=share_id, items=items,
                               images=images, all_images=len(images) == len(items),
                               failed=request.args.get("failed", type=int) or 0)

    @app.get("/api/shares/<share_id>")
    def share_items(share_id):
        return jsonify(items=load_share(share_id))

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
    @idempotent
    def document_print(job_id):
        job_dir(job_id)
        options = request.get_json(silent=True) or {}
        jobs = print_documents([{"id": job_id, "pages": options.get("pages")}], options)
        return jsonify(job=jobs[0])

    @app.post("/api/documents/print")
    @idempotent
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

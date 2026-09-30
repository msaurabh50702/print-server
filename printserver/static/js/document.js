"use strict";

(() => {
  const MAX_DOCS = window.MAX_DOCS || 20;
  const input = document.getElementById("doc-input");
  const moreInput = document.getElementById("more-input");
  const dropzone = document.getElementById("dropzone");
  const progress = document.getElementById("upload-progress");
  const list = document.getElementById("doc-list");
  const template = document.getElementById("doc-item");
  const printBtn = document.getElementById("print-doc");
  let docs = [];  // { id, name, pages, el }
  let uploading = false;

  // Two-sided only for printers that support it (e.g. not most home inkjets).
  document.addEventListener("printerchange", (e) => {
    const info = e.detail;
    const two = document.querySelector('input[name="sides"][value="two"]');
    const noDuplex = !!info && !info.duplex;
    two.disabled = noDuplex;
    document.getElementById("duplex-note").hidden = !noDuplex;
    if (noDuplex && two.checked) document.querySelector('input[name="sides"][value="one"]').checked = true;
  });

  /* ---------- choosing files ---------- */

  input.addEventListener("change", () => addFiles(input.files, input));
  moreInput.addEventListener("change", () => addFiles(moreInput.files, moreInput));

  ["dragenter", "dragover"].forEach((t) => document.addEventListener(t, (e) => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    dropzone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((t) => document.addEventListener(t, () => dropzone.classList.remove("drag")));
  document.addEventListener("drop", (e) => {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  });

  async function addFiles(fileList, fromInput) {
    const files = [...fileList];
    if (fromInput) fromInput.value = "";
    if (!files.length || uploading) return;
    const room = MAX_DOCS - docs.length;
    if (room <= 0) {
      toast(`You can print up to ${MAX_DOCS} documents at once`, "error");
      return;
    }
    if (files.length > room) toast(`Only the first ${room} files were added (limit ${MAX_DOCS})`, "error");

    uploading = true;
    printBtn.disabled = true;
    const failed = [];
    const batch = files.slice(0, room);
    for (let i = 0; i < batch.length; i++) {
      const file = batch[i];
      const label = batch.length > 1 ? `${i + 1} of ${batch.length}: ${file.name}` : file.name;
      if (!allowedFile(file)) {
        failed.push(`${file.name}: unsupported file type`);
        continue;
      }
      // Without the server, keep the file on the phone; it's uploaded when printed.
      if (!serverOnline) {
        await addLocalDoc(file);
        continue;
      }
      try {
        const data = await upload(file, (fraction) => setProgress(
          fraction === null ? null : (i + fraction) / batch.length,
          fraction === null ? `Preparing ${label}…` : `Uploading ${label}…`));
        addDoc({ ...data, file, uploadedAt: Date.now() });
      } catch (err) {
        if (err.offline) {
          setServerOnline(false);
          await addLocalDoc(file);
        } else {
          failed.push(`${file.name}: ${err.message}`);
        }
      }
    }
    progress.hidden = true;
    uploading = false;
    printBtn.disabled = false;
    if (failed.length) toast(failed.join(" · "), "error");
    render();
  }

  function setProgress(fraction, text) {
    progress.hidden = false;
    progress.classList.toggle("indeterminate", fraction === null);
    progress.querySelector(".bar i").style.setProperty("--p", `${Math.round((fraction || 0) * 100)}%`);
    progress.querySelector(".progress-text").textContent = text;
  }

  function upload(file, onProgress) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/documents");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded < e.total ? e.loaded / e.total : null);
      };
      const offline = () => Object.assign(new Error(Outbox.UNREACHABLE), { offline: true });
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
        if (xhr.status === 200) resolve(data);
        else if (xhr.status >= 500) reject(offline());
        else reject(new Error(data.error || "Upload failed"));
      };
      xhr.onerror = () => reject(offline());
      onProgress(0);
      xhr.send(form);
    });
  }

  /* ---------- files kept on the phone (server not reachable) ---------- */

  const ALLOWED = input.accept.split(",").map((ext) => ext.trim().toLowerCase());
  const extension = (name) => (/\.[^.]+$/.exec(name || "") || [""])[0].toLowerCase();
  const allowedFile = (file) => ALLOWED.includes(extension(file.name)) || /^image\/(jpeg|png|webp|gif)$/.test(file.type);
  const isPdf = (file) => extension(file.name) === ".pdf" || file.type === "application/pdf";
  const isImage = (file) => /^image\/(jpeg|png|webp|gif)$/.test(file.type) ||
    [".jpg", ".jpeg", ".png", ".webp", ".gif"].includes(extension(file.name));

  // pdf.js (kept on the phone with the app) shows PDFs without the server.
  let pdfjs = null;
  function loadPdfJs() {
    pdfjs = pdfjs || new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/vendor/pdfjs/pdf.min.js";
      script.onload = () => {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.js";
        resolve(window.pdfjsLib);
      };
      script.onerror = () => { pdfjs = null; reject(new Error("PDF preview isn't available")); };
      document.head.appendChild(script);
    });
    return pdfjs;
  }

  function openPdf(doc) {
    doc.pdf = doc.pdf || loadPdfJs().then(async (lib) =>
      lib.getDocument({ data: new Uint8Array(await doc.file.arrayBuffer()) }).promise);
    return doc.pdf;
  }

  async function renderPdfPage(doc, n) {
    const page = await (await openPdf(doc)).getPage(n);
    const base = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: Math.min(2, 900 / base.width) });
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(viewport.width);
    canvas.height = Math.round(viewport.height);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport }).promise;
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    return URL.createObjectURL(blob);
  }

  async function addLocalDoc(file) {
    const data = { name: file.name, file, local: true, image: isImage(file), pages: null };
    if (data.image) data.pages = 1;
    else if (isPdf(file)) {
      try {
        data.pages = (await openPdf(data)).numPages;
      } catch (_) {
        data.pdf = null;  // damaged or password-protected: the server will tell when it's sent
      }
    }
    addDoc(data);
  }

  // Picture of page n: from the server, or made on the phone for local files.
  function pageSource(doc, n) {
    if (!doc.local) return Promise.resolve(`/api/documents/${doc.id}/page/${n}.png`);
    doc.urls = doc.urls || {};
    if (!doc.urls[n]) {
      if (doc.image) doc.urls[n] = Promise.resolve(URL.createObjectURL(doc.file));
      else if (doc.pdf) doc.urls[n] = renderPdfPage(doc, n);
      else doc.urls[n] = Promise.reject(new Error("No preview"));
    }
    return doc.urls[n];
  }

  function forget(doc) {
    if (doc.openUrl) URL.revokeObjectURL(doc.openUrl);
    Object.values(doc.urls || {}).forEach((p) => p.then(URL.revokeObjectURL, () => {}));
  }

  /* ---------- list ---------- */

  function pagesText(doc) {
    if (doc.pages) return `${doc.pages} page${doc.pages === 1 ? "" : "s"}${doc.local ? " · on this phone" : ""}`;
    return "On this phone · preview when the server is reachable";
  }

  function addDoc(data) {
    const el = template.content.firstElementChild.cloneNode(true);
    const doc = { ...data, el };
    el.querySelector(".doc-title").textContent = data.name;
    el.querySelector(".doc-pages").textContent = pagesText(doc);
    const open = el.querySelector('[data-act="open"]');
    if (!doc.local) open.href = `/api/documents/${data.id}/pdf`;
    else if (doc.pdf || doc.image) open.href = doc.openUrl = URL.createObjectURL(doc.file);
    else open.hidden = true;
    const thumb = el.querySelector(".doc-thumb img");
    const noImage = () => el.querySelector(".doc-thumb").classList.add("no-img");
    thumb.addEventListener("load", () => thumb.classList.add("loaded"), { once: true });
    thumb.addEventListener("error", noImage, { once: true });
    pageSource(doc, 1).then((src) => { thumb.src = src; }, noImage);

    el.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn || btn.dataset.act === "open") return;
      const i = docs.indexOf(doc);
      if (btn.dataset.act === "remove") forget(docs.splice(i, 1)[0]);
      else if (btn.dataset.act === "up" && i > 0) [docs[i - 1], docs[i]] = [docs[i], docs[i - 1]];
      else if (btn.dataset.act === "down" && i < docs.length - 1) [docs[i + 1], docs[i]] = [docs[i], docs[i + 1]];
      else if (btn.dataset.act === "preview") return togglePreview(doc);
      render();
    });
    docs.push(doc);
  }

  function togglePreview(doc) {
    const box = doc.el.querySelector(".doc-preview");
    const label = doc.el.querySelector('.doc-links [data-act="preview"] span');
    box.hidden = !box.hidden;
    label.textContent = box.hidden ? "Preview pages" : "Hide preview";
    const pages = box.querySelector(".pages");
    if (box.hidden || pages.childElementCount) return;
    if (!doc.pages) {
      pages.innerHTML = `<div class="no-preview">Preview isn't available for this file until the printer server is reachable. It will still print.</div>`;
      return;
    }
    for (let n = 1; n <= doc.pages; n++) {
      const fig = document.createElement("figure");
      fig.className = "page-thumb";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("aria-label", `Enlarge page ${n}`);
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = `Page ${n}`;
      img.addEventListener("load", () => img.classList.add("loaded"), { once: true });
      const failed = () => {
        btn.outerHTML = `<div class="no-preview">Preview not available.<br>Use “Open PDF” to view.</div>`;
      };
      img.addEventListener("error", failed, { once: true });
      pageSource(doc, n).then((src) => { img.src = src; }, failed);
      btn.addEventListener("click", () => zoom(img.src));
      btn.appendChild(img);
      const cap = document.createElement("figcaption");
      cap.textContent = `Page ${n}`;
      fig.append(btn, cap);
      pages.appendChild(fig);
    }
  }

  function render() {
    const hasDocs = docs.length > 0;
    document.getElementById("step-upload").hidden = hasDocs;
    document.getElementById("step-preview").hidden = !hasDocs;
    // Re-appending existing nodes reorders them without losing input values.
    docs.forEach((doc, i) => {
      doc.el.querySelector('[data-act="up"]').disabled = i === 0;
      doc.el.querySelector('[data-act="down"]').disabled = i === docs.length - 1;
      list.appendChild(doc.el);
    });
    [...list.children].forEach((el) => { if (!docs.some((d) => d.el === el)) el.remove(); });
    const pages = docs.reduce((sum, d) => sum + (d.pages || 0), 0);
    const known = docs.every((d) => d.pages);
    document.getElementById("doc-summary").textContent =
      `${docs.length} document${docs.length === 1 ? "" : "s"}` +
      (known ? ` · ${pages} page${pages === 1 ? "" : "s"}` : "");
    document.getElementById("print-label").textContent = docs.length > 1 ? `Print all (${docs.length})` : "Print";
  }

  function zoom(src) {
    const view = document.createElement("div");
    view.className = "page-view";
    const img = document.createElement("img");
    img.alt = "";
    img.src = src;
    view.appendChild(img);
    const close = () => { view.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => e.key === "Escape" && close();
    view.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(view);
  }

  /* ---------- print ---------- */

  // Files shared from another app arrive already uploaded.
  takeSharedItems().then((items) => {
    items.forEach(addDoc);
    if (items.length) render();
  });

  // Same rules as the server (printing.parse_page_ranges), so mistakes show
  // up now rather than when a saved job is sent later.
  function checkRange(text, pages) {
    const value = text.replace(/\s+/g, "");
    if (!value) return null;
    for (const chunk of value.split(",")) {
      const match = /^(\d+)(?:-(\d+))?$/.exec(chunk);
      if (!match) return `Invalid page range '${chunk}'`;
      const start = Number(match[1]), end = Number(match[2] || match[1]);
      if (start < 1 || end < start || (pages && end > pages)) {
        return pages ? `Page range '${chunk}' is outside 1-${pages}` : `Invalid page range '${chunk}'`;
      }
    }
    return null;
  }

  printBtn.addEventListener("click", async () => {
    if (!docs.length || uploading) return;
    for (const doc of docs) {
      const problem = checkRange(doc.el.querySelector(".doc-range").value, doc.pages);
      if (problem) {
        toast(`${doc.name}: ${problem}`, "error");
        return;
      }
    }
    await submitPrintJob({
      title: docs.length === 1 ? docs[0].name : `${docs.length} documents`,
      docs: docs.map((d) => ({
        id: d.id, uploadedAt: d.uploadedAt, file: d.file, name: d.name,
        range: d.el.querySelector(".doc-range").value.trim(),
      })),
      options: {
        copies: document.getElementById("copies").value || 1,
        duplex: radioValue("sides") === "two",
        fit_to_page: radioValue("scale") === "fit",
        ...printTarget(),
      },
      button: printBtn,
      count: docs.length,
    });
  });
})();

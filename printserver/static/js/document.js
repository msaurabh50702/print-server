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
      const label = batch.length > 1 ? `${i + 1} of ${batch.length}: ${batch[i].name}` : batch[i].name;
      try {
        const data = await upload(batch[i], (fraction) => setProgress(
          fraction === null ? null : (i + fraction) / batch.length,
          fraction === null ? `Preparing ${label}…` : `Uploading ${label}…`));
        addDoc(data);
      } catch (err) {
        failed.push(`${batch[i].name}: ${err.message}`);
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
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
        if (xhr.status === 200) resolve(data);
        else reject(new Error(data.error || "Upload failed"));
      };
      xhr.onerror = () => reject(new Error("Upload failed. Is the server reachable?"));
      onProgress(0);
      xhr.send(form);
    });
  }

  /* ---------- list ---------- */

  function pageUrl(doc, n) {
    return `/api/documents/${doc.id}/page/${n}.png`;
  }

  function addDoc(data) {
    const el = template.content.firstElementChild.cloneNode(true);
    const doc = { ...data, el };
    el.querySelector(".doc-title").textContent = data.name;
    el.querySelector(".doc-pages").textContent = `${data.pages} page${data.pages === 1 ? "" : "s"}`;
    el.querySelector('[data-act="open"]').href = `/api/documents/${data.id}/pdf`;
    const thumb = el.querySelector(".doc-thumb img");
    thumb.addEventListener("load", () => thumb.classList.add("loaded"), { once: true });
    thumb.addEventListener("error", () => el.querySelector(".doc-thumb").classList.add("no-img"), { once: true });
    thumb.src = pageUrl(doc, 1);

    el.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn || btn.dataset.act === "open") return;
      const i = docs.indexOf(doc);
      if (btn.dataset.act === "remove") docs.splice(i, 1);
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
      img.addEventListener("error", () => {
        btn.outerHTML = `<div class="no-preview">Preview not available.<br>Use “Open PDF” to view.</div>`;
      }, { once: true });
      img.src = pageUrl(doc, n);
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
    const pages = docs.reduce((sum, d) => sum + d.pages, 0);
    document.getElementById("doc-summary").textContent =
      `${docs.length} document${docs.length === 1 ? "" : "s"} · ${pages} page${pages === 1 ? "" : "s"}`;
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

  printBtn.addEventListener("click", async () => {
    if (!docs.length || uploading) return;
    setBusy(printBtn, true);
    try {
      const res = await fetch("/api/documents/print", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          documents: docs.map((d) => ({ id: d.id, pages: d.el.querySelector(".doc-range").value })),
          copies: document.getElementById("copies").value || 1,
          duplex: radioValue("sides") === "two",
          ...printTarget(),
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const { jobs } = await res.json();
      toast(sentMessage(jobs.length), "success");
      refreshStatus();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(printBtn, false);
    }
  });
})();

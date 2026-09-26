"use strict";

(() => {
  const input = document.getElementById("doc-input");
  const dropzone = document.getElementById("dropzone");
  const progress = document.getElementById("upload-progress");
  const pagesEl = document.getElementById("pages");
  const rangeRow = document.getElementById("range-row");
  const rangeInput = document.getElementById("pages-range");
  let doc = null;  // { id, name, pages }

  input.addEventListener("change", () => input.files[0] && upload(input.files[0]));

  ["dragenter", "dragover"].forEach((t) => dropzone.addEventListener(t, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((t) => dropzone.addEventListener(t, () => dropzone.classList.remove("drag")));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
  });

  document.getElementById("other-doc").addEventListener("click", () => {
    input.value = "";
    document.getElementById("step-preview").hidden = true;
    document.getElementById("step-upload").hidden = false;
  });

  document.querySelectorAll('input[name="range"]').forEach((r) => r.addEventListener("change", () => {
    rangeRow.hidden = radioValue("range") !== "custom";
    if (!rangeRow.hidden) rangeInput.focus();
  }));

  function setProgress(fraction, text) {
    progress.hidden = false;
    progress.classList.toggle("indeterminate", fraction === null);
    progress.querySelector(".bar i").style.setProperty("--p", `${Math.round((fraction || 0) * 100)}%`);
    progress.querySelector(".progress-text").textContent = text;
  }

  function upload(file) {
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const done = e.loaded / e.total;
      if (done < 1) setProgress(done, `Uploading… ${Math.round(done * 100)}%`);
      else setProgress(null, "Preparing preview…");
    };
    xhr.onload = () => {
      progress.hidden = true;
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
      if (xhr.status !== 200) {
        toast(data.error || "Upload failed", "error");
        input.value = "";
        return;
      }
      showPreview(data);
    };
    xhr.onerror = () => { progress.hidden = true; toast("Upload failed. Is the server reachable?", "error"); };
    setProgress(0, "Uploading…");
    xhr.send(form);
  }

  function showPreview(data) {
    doc = data;
    document.getElementById("step-upload").hidden = true;
    document.getElementById("step-preview").hidden = false;
    document.getElementById("doc-name").textContent = data.name;
    document.getElementById("doc-pages").textContent = `${data.pages} page${data.pages === 1 ? "" : "s"}`;
    document.getElementById("open-pdf").href = `/api/documents/${data.id}/pdf`;
    rangeInput.value = "";
    document.querySelector('input[name="range"][value="all"]').checked = true;
    rangeRow.hidden = true;

    pagesEl.innerHTML = "";
    for (let n = 1; n <= data.pages; n++) {
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
      img.src = `/api/documents/${data.id}/page/${n}.png`;
      btn.addEventListener("click", () => zoom(img.src));
      btn.appendChild(img);
      const cap = document.createElement("figcaption");
      cap.textContent = `Page ${n}`;
      fig.append(btn, cap);
      pagesEl.appendChild(fig);
    }
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

  const printBtn = document.getElementById("print-doc");
  printBtn.addEventListener("click", async () => {
    if (!doc) return;
    const custom = radioValue("range") === "custom";
    if (custom && !rangeInput.value.trim()) {
      toast("Enter the page numbers to print", "error");
      rangeInput.focus();
      return;
    }
    setBusy(printBtn, true);
    try {
      const res = await fetch(`/api/documents/${doc.id}/print`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          copies: document.getElementById("copies").value || 1,
          pages: custom ? rangeInput.value : "",
          duplex: radioValue("sides") === "two",
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      toast("Sent to printer", "success");
      refreshStatus();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(printBtn, false);
    }
  });
})();

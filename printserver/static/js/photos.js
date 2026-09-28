"use strict";

(() => {
  const { margin_mm: MARGIN_MM, gap_mm: GAP_MM, page_w_mm: PAGE_W_MM } = window.SHEET;
  const layouts = Object.fromEntries(window.LAYOUTS.map((l) => [l.id, l]));
  const editor = new PhotoEditor();
  const sheet = document.getElementById("sheet");
  const fileInput = document.getElementById("file-input");
  const fillCount = document.getElementById("fill-count");
  const fitMode = () => radioValue("fit");

  let layout = null;
  let cells = [];          // per cell: { file, state, blob, url } or null
  let pendingCell = null;  // cell index waiting for a file from the picker

  const aspect = () => layout.cell_w_mm / layout.cell_h_mm;

  /* ---------- layout choice ---------- */

  document.querySelectorAll(".layout-btn").forEach((btn) =>
    btn.addEventListener("click", () => chooseLayout(btn.dataset.layout)));

  document.getElementById("change-layout").addEventListener("click", () => {
    document.getElementById("step-grid").hidden = true;
    document.getElementById("step-layout").hidden = false;
    setStep(1);
  });

  function setStep(n) {
    [1, 2, 3].forEach((i) => {
      const li = document.getElementById(`st-${i}`);
      li.classList.toggle("done", i < n);
      li.classList.toggle("current", i === n);
    });
  }

  function updateProgress() {
    const filled = cells.filter(Boolean).length;
    fillCount.textContent = filled
      ? `${filled} of ${cells.length} photos added`
      : "Tap a box to add a photo";
    setStep(filled ? 3 : 2);
  }

  async function chooseLayout(id) {
    const previous = cells.filter(Boolean);
    layout = layouts[id];
    cells = new Array(layout.cols * layout.rows).fill(null);
    document.getElementById("step-layout").hidden = true;
    document.getElementById("step-grid").hidden = false;
    renderSheet();
    updateProgress();

    // Carry existing photos over, re-cropped to the new box shape.
    for (let i = 0; i < previous.length && i < cells.length; i++) {
      const old = previous[i];
      URL.revokeObjectURL(old.url);
      const state = { ...old.state, crop: null };
      const result = await renderEdit(old.file, state, aspect());
      setCell(i, { file: old.file, ...result });
    }
    previous.slice(cells.length).forEach((c) => URL.revokeObjectURL(c.url));
  }

  /* ---------- sheet ---------- */

  function sizeSheet() {
    const pxPerMm = sheet.clientWidth / PAGE_W_MM;
    sheet.style.padding = `${MARGIN_MM * pxPerMm}px`;
    sheet.style.gap = `${GAP_MM * pxPerMm}px`;
  }
  window.addEventListener("resize", () => layout && sizeSheet());

  function renderSheet() {
    sheet.style.gridTemplateColumns = `repeat(${layout.cols}, 1fr)`;
    sheet.style.gridTemplateRows = `repeat(${layout.rows}, 1fr)`;
    sheet.classList.toggle("fit", fitMode() === "fit");
    sheet.innerHTML = "";
    cells.forEach((_, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cell";
      btn.addEventListener("click", () => cellClicked(i));
      sheet.appendChild(btn);
      paintCell(i);
    });
    sizeSheet();
  }

  function paintCell(i) {
    const btn = sheet.children[i];
    const cell = cells[i];
    btn.classList.toggle("filled", !!cell);
    btn.setAttribute("aria-label", cell ? `Edit photo ${i + 1}` : `Add photo ${i + 1}`);
    btn.innerHTML = cell ? `<img alt="" src="${cell.url}">` : `<span class="plus" aria-hidden="true">+</span>`;
  }

  function setCell(i, data) {
    if (cells[i]) URL.revokeObjectURL(cells[i].url);
    cells[i] = data ? { ...data, url: URL.createObjectURL(data.blob) } : null;
    paintCell(i);
    updateProgress();
  }

  document.querySelectorAll('input[name="fit"]').forEach((r) =>
    r.addEventListener("change", () => sheet.classList.toggle("fit", fitMode() === "fit")));

  /* ---------- picking & editing ---------- */

  function cellClicked(i) {
    if (cells[i]) openEditor(i, cells[i].file, cells[i].state, false);
    else pickFile(i);
  }

  function pickFile(i) {
    pendingCell = i;
    fileInput.value = "";
    fileInput.click();
  }

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file && pendingCell !== null) openEditor(pendingCell, file, null, true);
    pendingCell = null;
  });

  function openEditor(i, file, state, isNew) {
    editor.open({
      file, state, isNew, aspect: aspect(),
      onDone: (res) => setCell(i, res),
      onAll: (res) => {
        setCell(i, res);
        cells.forEach((c, j) => { if (!c) setCell(j, res); });
      },
      onRemove: () => setCell(i, null),
      onReplace: () => pickFile(i),
    });
  }

  /* ---------- output ---------- */

  function buildForm() {
    const form = new FormData();
    form.append("layout", layout.id);
    form.append("fit", fitMode());
    form.append("copies", document.getElementById("copies").value || "1");
    appendPrintTarget(form);
    let count = 0;
    cells.forEach((c, i) => { if (c) { form.append(`cell${i}`, c.blob, `cell${i}.jpg`); count++; } });
    if (!count) { toast("Tap a box to add a photo first", "error"); return null; }
    return form;
  }

  async function send(url, button) {
    const form = buildForm();
    if (!form) return null;
    setBusy(button, true);
    try {
      const res = await fetch(url, { method: "POST", body: form });
      if (!res.ok) throw new Error(await readError(res));
      return res;
    } catch (err) {
      toast(err.message, "error");
      return null;
    } finally {
      setBusy(button, false);
    }
  }

  const printBtn = document.getElementById("print-photos");
  printBtn.addEventListener("click", async () => {
    const res = await send("/api/photos/print", printBtn);
    if (res) {
      toast(sentMessage(), "success");
      refreshStatus();
    }
  });

  const pdfBtn = document.getElementById("download-pdf");
  pdfBtn.addEventListener("click", async () => {
    const res = await send("/api/photos/pdf", pdfBtn);
    if (!res) return;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "photos.pdf";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });
})();

"use strict";

(() => {
  const sizes = Object.fromEntries(window.PP_SIZES.map((s) => [s.id, s]));
  const { margin_mm: MARGIN, gap_mm: GAP, page_w_mm: PAGE_W, page_h_mm: PAGE_H } = window.SHEET;
  const editor = new PhotoEditor();
  const fileInput = document.getElementById("file-input");
  const slot = document.getElementById("pp-slot");
  const sheet = document.getElementById("pp-sheet");
  const emptySlot = slot.innerHTML;
  let photo = null;  // { file, state, blob, url }

  const size = () => sizes[radioValue("size")];
  const aspect = () => size().w_mm / size().h_mm;

  function photoCount() {
    const value = radioValue("count");
    return value === "full" ? size().max : Math.min(Number(value), size().max);
  }

  /* ---------- photo ---------- */

  slot.addEventListener("click", () => {
    if (photo) openEditor(photo.file, photo.state, false);
    else pickFile();
  });

  function pickFile() {
    fileInput.value = "";
    fileInput.click();
  }

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) openEditor(fileInput.files[0], null, true);
  });

  function openEditor(file, state, isNew) {
    editor.open({
      file, state, isNew, aspect: aspect(), faceGuide: true,
      onDone: setPhoto,
      onRemove: () => setPhoto(null),
      onReplace: pickFile,
    });
  }

  function setPhoto(data) {
    if (photo) URL.revokeObjectURL(photo.url);
    photo = data ? { ...data, url: URL.createObjectURL(data.blob) } : null;
    slot.classList.toggle("filled", !!photo);
    slot.setAttribute("aria-label", photo ? "Edit photo" : "Add photo");
    slot.innerHTML = photo ? `<img alt="" src="${photo.url}">` : emptySlot;
    renderSheet();
  }

  /* ---------- size & preview ---------- */

  document.querySelectorAll('input[name="size"]').forEach((r) => r.addEventListener("change", async () => {
    slot.style.setProperty("--ar", aspect());
    renderSheet();
    // Re-crop the existing photo to the new shape.
    if (photo) {
      const result = await renderEdit(photo.file, { ...photo.state, crop: null }, aspect());
      setPhoto({ file: photo.file, ...result });
    }
  }));
  document.querySelectorAll('input[name="count"]').forEach((r) => r.addEventListener("change", renderSheet));

  function renderSheet() {
    const s = size();
    const count = photoCount();
    document.getElementById("full-label").textContent = `Full (${s.max})`;
    sheet.innerHTML = "";
    for (let i = 0; i < count; i++) {
      const col = i % s.cols, row = Math.floor(i / s.cols);
      const tile = document.createElement("div");
      tile.className = "pp-tile";
      tile.style.left = `${((MARGIN + col * (s.w_mm + GAP)) / PAGE_W) * 100}%`;
      tile.style.top = `${((MARGIN + row * (s.h_mm + GAP)) / PAGE_H) * 100}%`;
      tile.style.width = `${(s.w_mm / PAGE_W) * 100}%`;
      tile.style.height = `${(s.h_mm / PAGE_H) * 100}%`;
      if (photo) tile.innerHTML = `<img alt="" src="${photo.url}">`;
      sheet.appendChild(tile);
    }
    document.getElementById("pp-summary").textContent =
      `${count} photo${count === 1 ? "" : "s"} of ${s.label} on one A4 page, printed at real size`;
  }

  /* ---------- output ---------- */

  function buildForm() {
    if (!photo) {
      toast("Add a photo first", "error");
      return null;
    }
    const form = new FormData();
    form.append("photo", photo.blob, "photo.jpg");
    form.append("size", radioValue("size"));
    form.append("count", radioValue("count"));
    form.append("outline", radioValue("outline"));
    form.append("copies", document.getElementById("copies").value || "1");
    appendPrintTarget(form);
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

  const printBtn = document.getElementById("print-pp");
  printBtn.addEventListener("click", async () => {
    if (await send("/api/passport/print", printBtn)) {
      toast(sentMessage(), "success");
      refreshStatus();
    }
  });

  const pdfBtn = document.getElementById("download-pdf");
  pdfBtn.addEventListener("click", async () => {
    const res = await send("/api/passport/pdf", pdfBtn);
    if (!res) return;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "passport-photos.pdf";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });

  slot.style.setProperty("--ar", aspect());
  renderSheet();
  // A photo shared from another app opens straight in the crop editor.
  takeSharedItems().then(async (items) => {
    const image = items.find((item) => item.image);
    if (!image) return;
    try {
      openEditor(await sharedImageFile(image), null, true);
    } catch (err) {
      toast(err.message, "error");
    }
  });
})();

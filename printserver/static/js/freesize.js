"use strict";

/*
 * Free size: place cropped photos anywhere on an A4 page at any size.
 * Positions and sizes are kept in millimetres; the page is drawn with
 * percentages so it scales to any screen.
 */
(() => {
  const { page_w_mm: PAGE_W, page_h_mm: PAGE_H, margin_mm: MARGIN, max_items: MAX_ITEMS } = window.SHEET;
  const MIN_MM = 5;          // smallest photo side
  const KEEP_ON_PAGE = 5;    // mm of a photo that must stay on the page while dragging
  const SNAP_MM = 2;         // snap to the page centre lines within this distance

  const editor = new PhotoEditor();
  const page = document.getElementById("fs-page");
  const emptyBtn = document.getElementById("fs-empty");
  const tools = document.getElementById("fs-tools");
  const inputW = document.getElementById("fs-w");
  const inputH = document.getElementById("fs-h");
  const fileInput = document.getElementById("file-input");
  const guideV = page.querySelector(".fs-guide.v");
  const guideH = page.querySelector(".fs-guide.h");

  let items = [];       // { file, state, blob, url, aspect, x, y, w, h, el }
  let selected = null;  // item
  let pendingReplace = null;

  const pxPerMm = () => page.clientWidth / PAGE_W;
  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
  const round1 = (v) => Math.round(v * 10) / 10;

  /* ---------- adding and editing photos ---------- */

  function pickFile(replaceItem = null) {
    if (!replaceItem && items.length >= MAX_ITEMS) {
      toast(`At most ${MAX_ITEMS} photos per page`, "error");
      return;
    }
    pendingReplace = replaceItem;
    fileInput.value = "";
    fileInput.click();
  }
  document.getElementById("fs-add").addEventListener("click", () => pickFile());
  emptyBtn.addEventListener("click", () => pickFile());

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    const replacing = pendingReplace;
    pendingReplace = null;
    openEditor(file, null, replacing);
  });

  // Crop/rotate a photo; free crop by default (no fixed shape).
  function openEditor(file, state, item) {
    editor.open({
      file, isNew: !item, aspect: null, noLock: true,
      state: state || { rotation: 0, crop: null, locked: false },
      onDone: (result) => (item ? updateItem(item, file, result) : addItem(file, result)),
      onRemove: () => item && removeItem(item),
      onReplace: () => pickFile(item),
    });
  }

  const cropAspect = (state) => state.crop.w / state.crop.h;

  function addItem(file, result, placement) {
    const aspect = cropAspect(result.state);
    let w = Math.min(120, PAGE_W - 2 * MARGIN);
    let h = w / aspect;
    if (h > PAGE_H - 2 * MARGIN) [h, w] = [PAGE_H - 2 * MARGIN, (PAGE_H - 2 * MARGIN) * aspect];
    const item = {
      file, state: result.state, blob: result.blob, url: URL.createObjectURL(result.blob), aspect,
      w, h, x: (PAGE_W - w) / 2, y: (PAGE_H - h) / 2, ...placement,
    };
    item.el = createElement(item);
    items.push(item);
    page.appendChild(item.el);
    select(item);
    render();
    return item;
  }

  function updateItem(item, file, result) {
    const cx = item.x + item.w / 2, cy = item.y + item.h / 2;
    URL.revokeObjectURL(item.url);
    Object.assign(item, { file, state: result.state, blob: result.blob,
                          url: URL.createObjectURL(result.blob), aspect: cropAspect(result.state) });
    item.h = item.w / item.aspect;
    item.x = cx - item.w / 2;
    item.y = cy - item.h / 2;
    item.el.querySelector("img").src = item.url;
    render();
  }

  function removeItem(item) {
    URL.revokeObjectURL(item.url);
    item.el.remove();
    items = items.filter((i) => i !== item);
    if (selected === item) selected = null;
    render();
  }

  function createElement(item) {
    const el = document.createElement("div");
    el.className = "fs-item";
    el.innerHTML = '<img alt="" draggable="false"><span class="fs-handle" aria-hidden="true"></span>';
    el.querySelector("img").src = item.url;
    el.addEventListener("pointerdown", (e) => startGesture(e, item));
    return el;
  }

  /* ---------- drawing ---------- */

  function render() {
    emptyBtn.hidden = items.length > 0;
    let outside = false;
    for (const item of items) {
      Object.assign(item.el.style, {
        left: `${(item.x / PAGE_W) * 100}%`, top: `${(item.y / PAGE_H) * 100}%`,
        width: `${(item.w / PAGE_W) * 100}%`, height: `${(item.h / PAGE_H) * 100}%`,
      });
      item.el.classList.toggle("selected", item === selected);
      if (item.x < MARGIN - 0.1 || item.y < MARGIN - 0.1 ||
          item.x + item.w > PAGE_W - MARGIN + 0.1 || item.y + item.h > PAGE_H - MARGIN + 0.1) outside = true;
    }
    document.getElementById("fs-warning").hidden = !outside;
    tools.hidden = !selected;
    if (selected && document.activeElement !== inputW && document.activeElement !== inputH) {
      inputW.value = round1(selected.w / 10);
      inputH.value = round1(selected.h / 10);
    }
  }

  function select(item) {
    selected = item;
    if (item) page.appendChild(item.el);  // bring to front
    render();
  }

  /* ---------- move, resize (corner) and pinch ---------- */

  const pointers = new Map();
  let gesture = null;

  function startGesture(e, item) {
    e.preventDefault();
    e.stopPropagation();
    select(item);
    page.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    beginFromPointers(e.target.classList.contains("fs-handle") ? "resize" : "move");
  }

  function beginFromPointers(mode) {
    const pts = [...pointers.values()];
    const it = selected;
    if (!it) return;
    if (pts.length >= 2) {
      gesture = { mode: "pinch", dist: Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y),
                  w: it.w, cx: it.x + it.w / 2, cy: it.y + it.h / 2 };
    } else {
      gesture = { mode, sx: pts[0].x, sy: pts[0].y, x: it.x, y: it.y, w: it.w };
    }
  }

  page.addEventListener("pointerdown", (e) => {
    // A second finger anywhere on the page turns a drag into a pinch.
    if (selected && pointers.size === 1 && !pointers.has(e.pointerId)) {
      page.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      beginFromPointers("pinch");
      return;
    }
    if (e.target === page || e.target.classList.contains("fs-margin")) select(null);
  });

  page.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId) || !gesture || !selected) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const it = selected;
    const scale = pxPerMm();
    let snapV = false, snapH = false;

    if (gesture.mode === "pinch") {
      const pts = [...pointers.values()];
      if (pts.length < 2) return;
      const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      setWidth(it, gesture.w * (dist / gesture.dist), gesture.cx, gesture.cy);
    } else if (gesture.mode === "resize") {
      const p = pointers.get(e.pointerId);
      const dx = (p.x - gesture.sx) / scale, dy = (p.y - gesture.sy) / scale;
      // Follow whichever direction the finger moved further, keeping the shape.
      const w = gesture.w + Math.max(dx, dy * it.aspect);
      it.w = clamp(w, MIN_MM, 2 * PAGE_H);
      it.h = it.w / it.aspect;
      if (it.h < MIN_MM) [it.h, it.w] = [MIN_MM, MIN_MM * it.aspect];
    } else {
      const p = pointers.get(e.pointerId);
      let x = gesture.x + (p.x - gesture.sx) / scale;
      let y = gesture.y + (p.y - gesture.sy) / scale;
      if (Math.abs(x + it.w / 2 - PAGE_W / 2) < SNAP_MM) { x = PAGE_W / 2 - it.w / 2; snapV = true; }
      if (Math.abs(y + it.h / 2 - PAGE_H / 2) < SNAP_MM) { y = PAGE_H / 2 - it.h / 2; snapH = true; }
      it.x = clamp(x, KEEP_ON_PAGE - it.w, PAGE_W - KEEP_ON_PAGE);
      it.y = clamp(y, KEEP_ON_PAGE - it.h, PAGE_H - KEEP_ON_PAGE);
    }
    guideV.hidden = !snapV;
    guideH.hidden = !snapH;
    render();
  });

  function endPointer(e) {
    if (!pointers.has(e.pointerId)) return;
    pointers.delete(e.pointerId);
    if (page.hasPointerCapture(e.pointerId)) page.releasePointerCapture(e.pointerId);
    guideV.hidden = guideH.hidden = true;
    // Lifting one finger of a pinch continues as a drag with the other.
    if (pointers.size === 1) beginFromPointers("move");
    else gesture = null;
  }
  page.addEventListener("pointerup", endPointer);
  page.addEventListener("pointercancel", endPointer);

  function setWidth(it, w, cx = it.x + it.w / 2, cy = it.y + it.h / 2) {
    it.w = clamp(w, Math.max(MIN_MM, MIN_MM * it.aspect), 2 * PAGE_H);
    it.h = it.w / it.aspect;
    it.x = cx - it.w / 2;
    it.y = cy - it.h / 2;
  }

  /* ---------- exact size and tools ---------- */

  function sizeInput(input, isWidth) {
    const cm = parseFloat(input.value);
    if (!selected || !(cm > 0)) return;
    const mm = clamp(cm * 10, MIN_MM, 2 * PAGE_H);
    setWidth(selected, isWidth ? mm : mm * selected.aspect);
    render();
    const other = isWidth ? inputH : inputW;
    other.value = round1((isWidth ? selected.h : selected.w) / 10);
  }
  inputW.addEventListener("input", () => sizeInput(inputW, true));
  inputH.addEventListener("input", () => sizeInput(inputH, false));
  for (const input of [inputW, inputH]) input.addEventListener("change", render);

  tools.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn || !selected) return;
    const it = selected;
    switch (btn.dataset.act) {
      case "edit":
        openEditor(it.file, it.state, it);
        return;
      case "center":
        it.x = (PAGE_W - it.w) / 2;
        it.y = (PAGE_H - it.h) / 2;
        break;
      case "fit": {
        const maxW = PAGE_W - 2 * MARGIN, maxH = PAGE_H - 2 * MARGIN;
        setWidth(it, Math.min(maxW, maxH * it.aspect), PAGE_W / 2, PAGE_H / 2);
        break;
      }
      case "duplicate":
        if (items.length >= MAX_ITEMS) {
          toast(`At most ${MAX_ITEMS} photos per page`, "error");
          return;
        }
        addItem(it.file, { state: it.state, blob: it.blob }, { w: it.w, h: it.h, ...duplicatePosition(it) });
        return;
      case "delete":
        removeItem(it);
        return;
    }
    render();
  });

  // Where to put a copy: next to the original if there's room, otherwise the
  // first free spot on the page, otherwise slightly offset from the original.
  function duplicatePosition(it) {
    const gap = 5;
    const fits = (x, y) =>
      x >= MARGIN - 0.01 && y >= MARGIN - 0.01 &&
      x + it.w <= PAGE_W - MARGIN + 0.01 && y + it.h <= PAGE_H - MARGIN + 0.01 &&
      items.every((o) => x + it.w + gap / 2 <= o.x || o.x + o.w + gap / 2 <= x ||
                         y + it.h + gap / 2 <= o.y || o.y + o.h + gap / 2 <= y);
    const near = [
      [it.x + it.w + gap, it.y], [it.x, it.y + it.h + gap],
      [it.x - it.w - gap, it.y], [it.x, it.y - it.h - gap],
    ];
    for (const [x, y] of near) if (fits(x, y)) return { x, y };
    for (let y = MARGIN; y + it.h <= PAGE_H - MARGIN; y += 2.5) {
      for (let x = MARGIN; x + it.w <= PAGE_W - MARGIN; x += 2.5) {
        if (fits(x, y)) return { x, y };
      }
    }
    toast("No free space left; the copy is placed on top");
    return { x: clamp(it.x + 8, 0, Math.max(0, PAGE_W - it.w)), y: clamp(it.y + 8, 0, Math.max(0, PAGE_H - it.h)) };
  }

  window.addEventListener("resize", render);

  /* ---------- output ---------- */

  function buildForm() {
    if (!items.length) {
      toast("Add a photo first", "error");
      return null;
    }
    const form = new FormData();
    form.append("items", JSON.stringify(items.map(({ x, y, w, h }) => ({ x, y, w, h }))));
    items.forEach((it, i) => form.append(`item${i}`, it.blob, `photo${i}.jpg`));
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
      toast(networkError(err).message, "error");
      return null;
    } finally {
      setBusy(button, false);
    }
  }

  const printBtn = document.getElementById("print-free");
  printBtn.addEventListener("click", () => {
    const form = buildForm();
    if (form) submitPrintJob({ title: `Free size (${items.length} photo${items.length === 1 ? "" : "s"})`,
                               url: "/api/free-size/print", form, button: printBtn });
  });

  const pdfBtn = document.getElementById("download-pdf");
  pdfBtn.addEventListener("click", async () => {
    const res = await send("/api/free-size/pdf", pdfBtn);
    if (!res) return;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "free-size.pdf";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });

  /* ---------- photos shared from another app ---------- */

  takeSharedItems().then(async (shared) => {
    const images = shared.filter((item) => item.image).slice(0, MAX_ITEMS);
    try {
      for (let i = 0; i < images.length; i++) {
        const file = await sharedImageFile(images[i]);
        const state = { rotation: 0, crop: null, locked: false };
        const result = await renderEdit(file, state, null);
        // Arrange in two columns so they don't all land on top of each other.
        const w = images.length === 1 ? 120 : 90;
        const aspect = cropAspect(result.state);
        const h = Math.min(w / aspect, 130);
        addItem(file, result, {
          w: h * aspect, h,
          x: images.length === 1 ? (PAGE_W - h * aspect) / 2 : MARGIN + (i % 2) * (PAGE_W / 2),
          y: images.length === 1 ? (PAGE_H - h) / 2 : MARGIN + Math.floor(i / 2) * 140,
        });
      }
      if (images.length) toast("Drag to move, pull the corner to resize");
    } catch (err) {
      toast(err.message, "error");
    }
  });

  render();
})();

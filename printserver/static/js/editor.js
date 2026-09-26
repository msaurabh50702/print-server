"use strict";

/*
 * Photo crop / rotate editor drawn on a <canvas>.
 *
 * Edit state: { rotation: 0|90|180|270, crop: {x, y, w, h} | null, locked: bool }
 * The crop is in pixels of the rotated working image, so a saved state can be
 * reapplied to the same file later ("edit again").
 */

const MAX_WORKING_SIDE = 3000;  // downscale huge camera photos while editing
const MAX_OUTPUT_SIDE = 2600;   // ~ A4 width at 300 dpi
const HANDLE_HIT = 28;          // CSS px

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Could not open this image")); };
    img.src = url;
  });
}

// Draw the (EXIF-oriented) image into a canvas, downscaled if huge.
async function workingCanvas(file) {
  const img = await loadImage(file);
  const scale = Math.min(1, MAX_WORKING_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
  const c = document.createElement("canvas");
  c.width = Math.round(img.naturalWidth * scale);
  c.height = Math.round(img.naturalHeight * scale);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return c;
}

function rotateCanvas(src, rotation) {
  if (!rotation) return src;
  const c = document.createElement("canvas");
  const swap = rotation % 180 !== 0;
  c.width = swap ? src.height : src.width;
  c.height = swap ? src.width : src.height;
  const ctx = c.getContext("2d");
  ctx.translate(c.width / 2, c.height / 2);
  ctx.rotate((rotation * Math.PI) / 180);
  ctx.drawImage(src, -src.width / 2, -src.height / 2);
  return c;
}

// Largest centred crop with the given aspect (w/h); whole image if aspect is null.
function defaultCrop(w, h, aspect) {
  if (!aspect) return { x: 0, y: 0, w, h };
  let cw = w, ch = w / aspect;
  if (ch > h) { ch = h; cw = h * aspect; }
  return { x: (w - cw) / 2, y: (h - ch) / 2, w: cw, h: ch };
}

function exportCrop(rotated, crop) {
  const scale = Math.min(1, MAX_OUTPUT_SIDE / Math.max(crop.w, crop.h));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(crop.w * scale));
  c.height = Math.max(1, Math.round(crop.h * scale));
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.drawImage(rotated, crop.x, crop.y, crop.w, crop.h, 0, 0, c.width, c.height);
  return new Promise((resolve) => c.toBlob(resolve, "image/jpeg", 0.92));
}

// Apply an edit state to a file without showing the editor.
async function renderEdit(file, state, aspect) {
  const rotated = rotateCanvas(await workingCanvas(file), state.rotation);
  const crop = state.crop || defaultCrop(rotated.width, rotated.height, state.locked ? aspect : null);
  return { blob: await exportCrop(rotated, crop), state: { ...state, crop } };
}

class PhotoEditor {
  constructor() {
    this.modal = document.getElementById("editor");
    this.stage = document.getElementById("ed-stage");
    this.canvas = document.getElementById("ed-canvas");
    this.ctx = this.canvas.getContext("2d");
    this.lockBtn = document.getElementById("ed-lock");

    const on = (id, fn) => document.getElementById(id).addEventListener("click", fn);
    on("ed-cancel", () => this.close("onCancel"));
    on("ed-rot-left", () => this.rotate(-90));
    on("ed-rot-right", () => this.rotate(90));
    on("ed-reset", () => { this.resetCrop(); this.draw(); });
    on("ed-lock", () => {
      this.state.locked = !this.state.locked;
      this.lockBtn.classList.toggle("active", this.state.locked);
      this.lockBtn.setAttribute("aria-pressed", String(this.state.locked));
      this.resetCrop();
      this.draw();
    });
    on("ed-done", () => this.finish("onDone"));
    on("ed-all", () => this.finish("onAll"));
    on("ed-remove", () => this.close("onRemove"));
    // Called synchronously so the file picker opens inside the user gesture.
    on("ed-replace", () => this.close("onReplace"));

    this.canvas.addEventListener("pointerdown", (e) => this.pointerDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.pointerMove(e));
    this.canvas.addEventListener("pointerup", (e) => this.pointerUp(e));
    this.canvas.addEventListener("pointercancel", (e) => this.pointerUp(e));
    window.addEventListener("resize", () => { if (!this.modal.hidden) this.draw(); });
    document.addEventListener("keydown", (e) => {
      if (!this.modal.hidden && e.key === "Escape") this.close("onCancel");
    });
  }

  /* opts: { file, state, aspect, isNew, faceGuide?, onDone(result), onAll(result)?, onRemove(), onReplace(), onCancel() }
     "Fill all" is only shown when onAll is given. */
  async open(opts) {
    this.opts = opts;
    this.aspect = opts.aspect;
    this.state = opts.state
      ? { ...opts.state, crop: opts.state.crop && { ...opts.state.crop } }
      : { rotation: 0, crop: null, locked: true };
    this.lockBtn.classList.toggle("active", this.state.locked);
    this.lockBtn.setAttribute("aria-pressed", String(this.state.locked));
    document.getElementById("ed-remove").hidden = !!opts.isNew;
    document.getElementById("ed-replace").hidden = !!opts.isNew;
    document.getElementById("ed-all").hidden = !opts.onAll;
    document.getElementById("ed-hint").textContent = opts.faceGuide
      ? "Fit the face inside the oval. Drag a corner to zoom."
      : "Drag the box to move it. Drag a corner to resize.";
    this.modal.hidden = false;
    this.modal.classList.add("loading");
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    document.body.style.overflow = "hidden";
    const token = (this.token = {});
    let base;
    try {
      base = await workingCanvas(opts.file);
    } catch (err) {
      if (token !== this.token) return;
      this.modal.classList.remove("loading");
      this.close("onCancel");
      toast(err.message, "error");
      return;
    }
    // Ignore a slow load if the editor was closed or reopened meanwhile.
    if (token !== this.token || this.modal.hidden) return;
    this.base = base;
    this.modal.classList.remove("loading");
    this.rotated = rotateCanvas(this.base, this.state.rotation);
    if (!this.state.crop) this.resetCrop();
    this.draw();
  }

  close(callbackName) {
    this.token = null;
    this.modal.hidden = true;
    document.body.style.overflow = "";
    const cb = this.opts && this.opts[callbackName];
    this.base = this.rotated = null;
    if (cb) cb();
  }

  async finish(callbackName) {
    if (!this.rotated) return;
    const blob = await exportCrop(this.rotated, this.state.crop);
    const result = { blob, state: this.state, file: this.opts.file };
    const cb = this.opts[callbackName];
    this.close(null);
    cb(result);
  }

  rotate(delta) {
    if (!this.base) return;
    this.state.rotation = (this.state.rotation + delta + 360) % 360;
    this.rotated = rotateCanvas(this.base, this.state.rotation);
    this.resetCrop();
    this.draw();
  }

  resetCrop() {
    if (!this.rotated) return;
    this.state.crop = defaultCrop(this.rotated.width, this.rotated.height,
      this.state.locked ? this.aspect : null);
  }

  /* ---------- drawing ---------- */

  layout() {
    const rect = this.stage.getBoundingClientRect();
    const pad = 16;
    const scale = Math.min((rect.width - 2 * pad) / this.rotated.width,
      (rect.height - 2 * pad) / this.rotated.height);
    return {
      scale,
      ox: (rect.width - this.rotated.width * scale) / 2,
      oy: (rect.height - this.rotated.height * scale) / 2,
      cssW: rect.width, cssH: rect.height,
    };
  }

  draw() {
    if (!this.rotated) return;
    const dpr = window.devicePixelRatio || 1;
    const L = (this.view = this.layout());
    this.canvas.width = Math.round(L.cssW * dpr);
    this.canvas.height = Math.round(L.cssH * dpr);
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, L.cssW, L.cssH);
    const iw = this.rotated.width * L.scale, ih = this.rotated.height * L.scale;
    ctx.drawImage(this.rotated, L.ox, L.oy, iw, ih);

    const c = this.state.crop;
    const x = L.ox + c.x * L.scale, y = L.oy + c.y * L.scale;
    const w = c.w * L.scale, h = c.h * L.scale;

    // Dim everything outside the crop.
    ctx.fillStyle = "rgba(0,0,0,.55)";
    ctx.beginPath();
    ctx.rect(L.ox, L.oy, iw, ih);
    ctx.rect(x, y, w, h);
    ctx.fill("evenodd");

    // Rule-of-thirds guides.
    ctx.strokeStyle = "rgba(255,255,255,.35)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 1; i < 3; i++) {
      ctx.moveTo(x + (w * i) / 3, y); ctx.lineTo(x + (w * i) / 3, y + h);
      ctx.moveTo(x, y + (h * i) / 3); ctx.lineTo(x + w, y + (h * i) / 3);
    }
    ctx.stroke();

    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);

    if (this.opts.faceGuide) {
      // Where the head should sit on a passport photo: roughly 70% of the
      // height from chin to crown, centred horizontally.
      ctx.save();
      ctx.setLineDash([6, 5]);
      ctx.strokeStyle = "rgba(255,255,255,.85)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.ellipse(x + w / 2, y + h * 0.46, w * 0.29, h * 0.35, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    // Corner handles.
    const s = 18;
    ctx.lineWidth = 4;
    ctx.beginPath();
    for (const [cx, cy, dx, dy] of [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]) {
      ctx.moveTo(cx, cy + dy * s); ctx.lineTo(cx, cy); ctx.lineTo(cx + dx * s, cy);
    }
    ctx.stroke();
  }

  /* ---------- interaction ---------- */

  toImage(e) {
    const rect = this.canvas.getBoundingClientRect();
    const L = this.view;
    return {
      x: (e.clientX - rect.left - L.ox) / L.scale,
      y: (e.clientY - rect.top - L.oy) / L.scale,
    };
  }

  pointerDown(e) {
    if (!this.rotated) return;
    const p = this.toImage(e);
    const c = this.state.crop;
    const tol = HANDLE_HIT / this.view.scale;
    const corners = [
      { x: c.x, y: c.y, ax: c.x + c.w, ay: c.y + c.h },
      { x: c.x + c.w, y: c.y, ax: c.x, ay: c.y + c.h },
      { x: c.x, y: c.y + c.h, ax: c.x + c.w, ay: c.y },
      { x: c.x + c.w, y: c.y + c.h, ax: c.x, ay: c.y },
    ];
    const corner = corners.find((k) => Math.abs(p.x - k.x) < tol && Math.abs(p.y - k.y) < tol);
    if (corner) {
      this.drag = { mode: "resize", ax: corner.ax, ay: corner.ay };
    } else if (p.x >= c.x && p.x <= c.x + c.w && p.y >= c.y && p.y <= c.y + c.h) {
      this.drag = { mode: "move", dx: p.x - c.x, dy: p.y - c.y };
    } else {
      return;
    }
    this.canvas.setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  pointerMove(e) {
    if (!this.drag) return;
    const p = this.toImage(e);
    const W = this.rotated.width, H = this.rotated.height;
    const c = this.state.crop;
    if (this.drag.mode === "move") {
      c.x = Math.min(Math.max(p.x - this.drag.dx, 0), W - c.w);
      c.y = Math.min(Math.max(p.y - this.drag.dy, 0), H - c.h);
    } else {
      const { ax, ay } = this.drag;
      const sx = p.x >= ax ? 1 : -1, sy = p.y >= ay ? 1 : -1;
      const maxW = sx > 0 ? W - ax : ax, maxH = sy > 0 ? H - ay : ay;
      const minSide = Math.max(20, Math.min(W, H) * 0.03);
      let w = Math.min(Math.abs(p.x - ax), maxW);
      let h = Math.min(Math.abs(p.y - ay), maxH);
      if (this.state.locked && this.aspect) {
        const a = this.aspect;
        if (w / h > a) w = h * a; else h = w / a;
        if (w < minSide) { w = minSide; h = w / a; }
        if (h < minSide) { h = minSide; w = h * a; }
        if (w > maxW) { w = maxW; h = w / a; }
        if (h > maxH) { h = maxH; w = h * a; }
      } else {
        w = Math.max(w, Math.min(minSide, maxW));
        h = Math.max(h, Math.min(minSide, maxH));
      }
      c.w = w; c.h = h;
      c.x = sx > 0 ? ax : ax - w;
      c.y = sy > 0 ? ay : ay - h;
    }
    this.draw();
  }

  pointerUp(e) {
    if (!this.drag) return;
    this.drag = null;
    if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
  }
}

"use strict";

let toastTimer;
function toast(message, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast show " + kind;
  if (kind === "success" && navigator.vibrate) navigator.vibrate(30);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast " + kind), 3500);
}

async function readError(response) {
  try {
    const data = await response.json();
    return data.error || response.statusText;
  } catch (_) {
    return response.statusText || "Request failed";
  }
}

// Show a spinner on a button while a task runs.
function setBusy(button, busy) {
  button.disabled = busy;
  button.classList.toggle("busy", busy);
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

const radioValue = (name) => document.querySelector(`input[name="${name}"]:checked`).value;

// +/- stepper buttons next to number inputs.
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".stepper [data-step]");
  if (!btn) return;
  const input = btn.parentElement.querySelector("input");
  const next = (parseInt(input.value, 10) || 1) + Number(btn.dataset.step);
  input.value = Math.min(Number(input.max) || 99, Math.max(Number(input.min) || 1, next));
});

/* ---------- printers ---------- */

const PRINTER_KEY = "printer";
let printersInfo = [];

function storedPrinter() {
  try { return localStorage.getItem(PRINTER_KEY) || ""; } catch (_) { return ""; }
}

// Printer in the dropdown; on pages without one, the phone's saved choice,
// falling back to the server's default printer.
function selectedPrinter() {
  const select = document.getElementById("printer-select");
  if (select && select.value) return select.value;
  const saved = storedPrinter();
  if (printersInfo.some((p) => p.name === saved)) return saved;
  return (typeof overviewData !== "undefined" && overviewData.default) || saved;
}

const selectedPrinterInfo = () => printersInfo.find((p) => p.name === selectedPrinter());
const printerLabel = (p) => (p ? p.description || p.name.replace(/_/g, " ") : "");

// Value of a paper/quality menu if it's shown for the current printer.
function choiceValue(kind) {
  const row = document.getElementById(`${kind}-row`);
  const select = document.getElementById(`${kind}-select`);
  return row && !row.hidden && select ? select.value : "";
}

// Printer, colour, paper and quality chosen on this page, sent with every print.
function printTarget() {
  const info = selectedPrinterInfo();
  const mono = document.querySelector('input[name="color"][value="mono"]');
  return {
    printer: selectedPrinter(),
    color: info && info.color && mono && mono.checked ? "mono" : "color",
    paper: choiceValue("paper"),
    quality: choiceValue("quality"),
  };
}

function appendPrintTarget(form) {
  for (const [key, value] of Object.entries(printTarget())) form.append(key, value);
}

// Paper type / print quality menus, remembered per printer on this phone.
const choiceKey = (kind, printer) => `${kind}:${printer}`;

function fillChoice(kind, info) {
  const row = document.getElementById(`${kind}-row`);
  const select = document.getElementById(`${kind}-select`);
  if (!row || !select) return;
  const set = info && info[kind];
  row.hidden = !set;
  if (!set) return;
  const signature = info.name + JSON.stringify(set.choices);
  if (select.dataset.signature === signature) return;
  select.dataset.signature = signature;
  select.replaceChildren(...set.choices.map((c) => new Option(c.label, c.value)));
  let saved = "";
  try { saved = localStorage.getItem(choiceKey(kind, info.name)) || ""; } catch (_) { /* private mode */ }
  const values = set.choices.map((c) => c.value);
  select.value = values.includes(saved) ? saved : (values.includes(set.default) ? set.default : values[0]);
}

// Show only the options the chosen printer supports; pages can listen for
// the "printerchange" event to adjust their own controls (e.g. two-sided).
function applyPrinterCaps() {
  const info = selectedPrinterInfo();
  const colorRow = document.getElementById("color-row");
  if (colorRow) colorRow.hidden = !(info && info.color);
  fillChoice("paper", info);
  fillChoice("quality", info);
  document.dispatchEvent(new CustomEvent("printerchange", { detail: info || null }));
}

// First alert of a printer ("Out of paper", "Black ink low"...), if any.
const printerAlert = (p) => (p && p.alerts && p.alerts[0]) || null;

function renderPrinterSelect(defaultName) {
  const select = document.getElementById("printer-select");
  if (!select) return;
  const options = printersInfo.map((p) => {
    let label = printerLabel(p);
    const alert = printerAlert(p);
    if (p.state === "disabled") label += " (offline)";
    else if (alert && alert.severity === "error") label += ` (${alert.text.toLowerCase()})`;
    else if (p.is_default && printersInfo.length > 1) label += " · default";
    return [p.name, label];
  });
  // Rebuild only when the list actually changed, so a background refresh
  // never disturbs someone who is choosing a printer.
  const signature = JSON.stringify(options);
  if (select.dataset.signature === signature) return false;
  const current = select.value || storedPrinter();
  select.dataset.signature = signature;
  select.replaceChildren(...(options.length
    ? options.map(([value, label]) => new Option(label, value))
    : [new Option("No printer set up", "")]));
  select.disabled = !options.length;
  select.value = options.some(([name]) => name === current) ? current : defaultName || "";
  return true;
}

function renderStatus() {
  const el = document.getElementById("printer-status");
  const info = selectedPrinterInfo();
  const alert = printerAlert(info);
  let text, state;
  if (!info) [text, state] = ["No printer", "bad"];
  else if (overviewData.dry_run) [text, state] = ["Test mode", "ok"];
  else if (!info.ok) [text, state] = ["Offline", "bad"];
  else if (alert && alert.severity === "error") [text, state] = [alert.text, "bad"];
  else if (info.queued) [text, state] = [`Printing (${info.queued})`, "ok"];
  else if (alert) [text, state] = [alert.text, "warn"];
  else [text, state] = ["Ready", "ok"];
  el.querySelector(".status-text").textContent = text;
  for (const cls of ["ok", "bad", "warn"]) el.classList.toggle(cls, cls === state);
  el.title = `${info ? printerLabel(info) : "No printer"} · Tap to see the print queue`;
}

// Apply printer data (embedded in the page, or from a refresh).
function applyOverview(data) {
  overviewData = data;
  printersInfo = data.printers || [];
  const changed = renderPrinterSelect(data.default);
  if (changed !== false) applyPrinterCaps();
  renderStatus();
  document.dispatchEvent(new CustomEvent("overview", { detail: data }));
}

let refreshing = null;
function refreshStatus() {
  // One request at a time; callers during a refresh share it.
  refreshing = refreshing || fetch("/api/printers")
    .then((res) => res.json())
    .then(applyOverview)
    .catch(() => {
      const el = document.getElementById("printer-status");
      el.querySelector(".status-text").textContent = "Server offline";
      el.classList.remove("ok");
      el.classList.add("bad");
    })
    .finally(() => { refreshing = null; });
  return refreshing;
}

document.addEventListener("change", (e) => {
  if (e.target.id === "paper-select" || e.target.id === "quality-select") {
    const kind = e.target.id.split("-")[0];
    try { localStorage.setItem(choiceKey(kind, selectedPrinter()), e.target.value); } catch (_) { /* private mode */ }
    return;
  }
  if (e.target.id !== "printer-select") return;
  try { localStorage.setItem(PRINTER_KEY, e.target.value); } catch (_) { /* private mode */ }
  applyPrinterCaps();
  renderStatus();
});

// Success message naming the printer the job went to.
function sentMessage(count = 1) {
  const name = printerLabel(selectedPrinterInfo()) || "printer";
  return count > 1 ? `Sent ${count} documents to ${name}` : `Sent to ${name}`;
}

// The page arrives with printer data embedded, so it shows immediately;
// after that, refresh quietly in the background.
let overviewData = window.BOOT || { printers: [], default: null, dry_run: false };
applyOverview(overviewData);
// Page scripts load after this file; tell them about the printer once they're listening.
document.addEventListener("DOMContentLoaded", applyPrinterCaps);
setInterval(() => document.visibilityState === "visible" && refreshStatus(), 10000);
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && refreshStatus());
// Pages restored from the back/forward cache carry old data.
window.addEventListener("pageshow", (e) => e.persisted && refreshStatus());

// Service worker: makes the app installable (browsers only allow this on HTTPS).
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* not critical */ });
}

/* ---------- files shared from other apps (Share → Printer) ---------- */

// Items from /share for this page (?share=<id>), or [] when there are none.
// The parameter is removed so a reload doesn't add the files again.
async function takeSharedItems() {
  const params = new URLSearchParams(location.search);
  const id = params.get("share");
  if (!id) return [];
  params.delete("share");
  history.replaceState(null, "", location.pathname + (params.toString() ? `?${params}` : ""));
  try {
    const res = await fetch(`/api/shares/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error();
    return (await res.json()).items;
  } catch (_) {
    toast("The shared files have expired. Please share them again.", "error");
    return [];
  }
}

// A shared image as a File, ready for the crop editor.
async function sharedImageFile(item) {
  const res = await fetch(`/api/documents/${item.id}/original`);
  if (!res.ok) throw new Error("Could not load the shared photo");
  const blob = await res.blob();
  return new File([blob], item.name, { type: blob.type });
}

/* ---------- photo fit preview (Whole photo / Fill box) ---------- */

// Previews show the whole photo or a filled box, matching what will print.
function syncPhotoFit() {
  const chosen = document.querySelector('input[name="fit"]:checked');
  document.body.classList.toggle("fit-whole", !!chosen && chosen.value === "fit");
}
document.addEventListener("change", (e) => e.target.name === "fit" && syncPhotoFit());
document.addEventListener("DOMContentLoaded", syncPhotoFit);

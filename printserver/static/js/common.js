"use strict";

let toastTimer;
function toast(message, kind = "", ms = 3500) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast show " + kind;
  if (kind === "success" && navigator.vibrate) navigator.vibrate(30);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast " + kind), ms);
}

// fetch() rejects with a TypeError when the server can't be reached at all.
const networkError = (err) =>
  err instanceof TypeError ? new Error("Printer server not reachable. Check the Wi-Fi and try again.") : err;

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
    else if (p.connected === false) label += " (switched off)";
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
  if (!serverOnline) [text, state] = [waitingJobs ? `Offline · ${waitingJobs} saved` : "Server offline", "bad"];
  else if (waitingJobs) [text, state] = [`Sending (${waitingJobs})`, "ok"];
  else if (!info) [text, state] = ["No printer", "bad"];
  else if (overviewData.dry_run) [text, state] = ["Test mode", "ok"];
  else if (!info.ok) [text, state] = ["Offline", "bad"];
  else if (info.connected === false) [text, state] = ["Switched off", "bad"];
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

/* ---------- the Pi's clock ---------- */

// Without internet the Pi's clock is behind after every power cut (it has no
// clock battery), which shows wrong job times and can even break HTTPS. The
// phone knows the right time, so it tells the Pi when they disagree.
let clockSent = false;
function syncClock(data) {
  if (clockSent || data.dry_run || !data.generated) return;
  if (Math.abs(Date.now() / 1000 - data.generated) < 120) return;
  clockSent = true;
  fetch("/api/clock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ now: Date.now() / 1000 }),
  }).then((res) => res.ok && res.json())
    .then((result) => result && result.adjusted && document.dispatchEvent(new CustomEvent("clockfixed")))
    .catch(() => { /* not critical */ });
}

/* ---------- server reachable? ---------- */

const OVERVIEW_KEY = "overview";
let serverOnline = true;
let waitingJobs = 0;     // print jobs saved on this phone, not sent yet

function setServerOnline(online) {
  serverOnline = online;
  document.body.classList.toggle("server-offline", !online);
  const banner = document.getElementById("offline-banner");
  if (banner) banner.hidden = online;
  renderStatus();
}

let refreshing = null;
function refreshStatus() {
  // One request at a time; callers during a refresh share it. Weak Wi-Fi
  // shouldn't leave the page waiting forever.
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 8000);
  // no-store: going back to a page, Chrome would otherwise reuse an old answer.
  refreshing = refreshing || fetch("/api/printers", { signal: abort.signal, cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      return res.json();
    })
    .then((data) => {
      try { localStorage.setItem(OVERVIEW_KEY, JSON.stringify(data)); } catch (_) { /* private mode */ }
      applyOverview(data);
      setServerOnline(true);
      syncClock(data);
      if (waitingJobs) sendSavedJobs();
    })
    .catch(() => setServerOnline(false))
    .finally(() => { clearTimeout(timer); refreshing = null; });
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
function sentMessage(count = 1, name = printerLabel(selectedPrinterInfo()) || "printer") {
  return count > 1 ? `Sent ${count} documents to ${name}` : `Sent to ${name}`;
}

/* ---------- print jobs saved on this phone (see outbox.js) ---------- */

const ownJobs = new Set();   // jobs this page is sending itself (it reports on them)
const PRINT_WAIT_MS = 20000; // longest the Print button spins before carrying on in the background

async function countSavedJobs() {
  try {
    waitingJobs = (await Outbox.all()).filter((job) => job.status !== "failed").length;
  } catch (_) {
    waitingJobs = 0;
  }
  renderStatus();
}

let sending = null;
function sendSavedJobs() {
  sending = sending || Outbox.flush()
    .then((report) => {
      if (report.sent.length) refreshStatus();
      if (report.pending && !report.sent.length) setServerOnline(false);
    })
    .catch(() => { /* storage unavailable */ })
    .finally(() => { sending = null; });
  return sending;
}

// Android can send saved jobs later even if the app is closed.
function sendInBackgroundLater() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.ready
    .then((reg) => reg.sync && reg.sync.register("outbox"))
    .catch(() => { /* not supported (e.g. iPhone): sent when the app is open */ });
}

if (window.Outbox) {
  Outbox.onChange((detail) => {
    countSavedJobs();
    const job = detail.sent;
    if (job && !ownJobs.has(job.key)) toast(`Saved job sent: ${job.title} → ${job.printerLabel}`, "success", 5000);
  });
}

/**
 * Print: the job is saved on this phone first, then sent. If the server
 * can't be reached it stays saved and is sent automatically later.
 * job: { title, url + form } or { title, docs + options } (documents).
 * Returns true when the server accepted the job.
 */
async function submitPrintJob({ title, url, form, docs, options, button, count = 1 }) {
  const label = printerLabel(selectedPrinterInfo()) || selectedPrinter() || "printer";
  const job = docs
    ? { kind: "documents", title, docs, options }
    : { kind: "form", title, url, fields: [...form.entries()] };
  Object.assign(job, { printer: selectedPrinter(), printerLabel: label, count });
  setBusy(button, true);
  let saved = false, result;
  try {
    try {
      await Outbox.add(job);
      saved = true;
      if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    } catch (_) {
      job.key = Outbox.newKey();   // no storage (e.g. private mode): send without saving
    }
    ownJobs.add(job.key);
    if (saved && !serverOnline) {
      // Known to be offline: just keep it; it's sent when the server is back.
      result = { retry: true };
      return finishPrint(job, saved, result, count, label);
    }
    const sending = saved ? Outbox.flush({ only: job.key }).then((r) => r.result) : Outbox.deliver(job);
    // Don't keep the button spinning on a very slow connection: the job is
    // saved and finishes in the background.
    result = await Promise.race([sending, new Promise((resolve) =>
      setTimeout(() => resolve(saved ? { slow: true } : undefined), PRINT_WAIT_MS))]) || await sending;
  } catch (err) {
    result = { failed: true, error: err.message };
  } finally {
    setBusy(button, false);
  }
  return finishPrint(job, saved, result, count, label);
}

async function finishPrint(job, saved, result, count, label) {
  result = result || { ok: true };
  if (result.deleted) return false;  // deleted from the queue page while sending
  if (result.slow) {
    ownJobs.delete(job.key);  // report it when it's done
    toast("Slow connection: still sending. The job is saved, so you can carry on; it finishes in the background.", "", 7000);
    return false;
  }
  if (result.ok) {
    toast(sentMessage(count, label), "success");
    setServerOnline(true);
    refreshStatus();
    return true;
  }
  if (result.retry && saved) {
    setServerOnline(false);
    sendInBackgroundLater();
    toast("Printer server not reachable. The job is saved on this phone and will print automatically when the server is back.", "", 7000);
    return false;
  }
  if (saved) await Outbox.remove(job.key).catch(() => {});
  toast(result.error, "error");
  return false;
}

// The page arrives with printer data embedded, so it shows immediately;
// after that, refresh quietly in the background.
let overviewData = window.BOOT || { printers: [], default: null, dry_run: false };
// A page opened from the copy saved on the phone carries old printer data;
// the latest the phone has seen is better.
try {
  const stored = JSON.parse(localStorage.getItem(OVERVIEW_KEY) || "null");
  if (stored && (stored.generated || 0) > (overviewData.generated || 0)) overviewData = stored;
} catch (_) { /* private mode */ }
applyOverview(overviewData);
// The page may have come from the copy saved on the phone, so check now
// whether the server can actually be reached (the server caches this, so it's quick).
refreshStatus();
if (window.Outbox) countSavedJobs().then(() => waitingJobs && serverOnline && sendSavedJobs());
window.addEventListener("online", () => refreshStatus());
// Page scripts load after this file; tell them about the printer once they're listening.
document.addEventListener("DOMContentLoaded", applyPrinterCaps);
setInterval(() => document.visibilityState === "visible" && refreshStatus(), 10000);
// Back to this page (another tab or app, or the back button restoring it from
// memory): saved jobs may have been deleted or sent meanwhile.
function recheck() {
  if (window.Outbox) countSavedJobs();
  refreshStatus();
}
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && recheck());
window.addEventListener("pageshow", (e) => e.persisted && recheck());

// Service worker: makes the app installable and keeps it on the phone for
// offline use (browsers only allow this on HTTPS).
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* not critical */ });
}

// Home page: say whether the app is saved on this phone yet (OFFLINE_PAGES in app.py).
const OFFLINE_PAGES = ["/", "/photos", "/free-size", "/passport", "/id-card", "/document", "/queue"];
let offlineChecks = 0;
async function showOfflineReady() {
  const el = document.getElementById("offline-ready");
  if (!el) return;
  el.hidden = false;
  if (!("serviceWorker" in navigator) || !window.isSecureContext || !window.caches) {
    el.textContent = "Offline use works in the app installed from the https:// address.";
    return;
  }
  let ready = false;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const saved = await Promise.all(OFFLINE_PAGES.map((url) => caches.match(url)));
    ready = !!(navigator.serviceWorker.controller && reg && !reg.installing && saved.every(Boolean));
    // A download interrupted by weak Wi-Fi is started again.
    if (!ready && reg && !reg.installing && ++offlineChecks % 15 === 0) reg.update().catch(() => {});
  } catch (_) { /* treat as not ready */ }
  el.classList.toggle("ok", ready);
  el.textContent = ready
    ? "✓ Saved on this phone: works offline"
    : "Saving the app for offline use… keep it open on Wi-Fi for a moment.";
  if (!ready) setTimeout(showOfflineReady, 2000);
}
showOfflineReady();

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

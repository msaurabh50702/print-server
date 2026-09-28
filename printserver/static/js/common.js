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

function selectedPrinter() {
  const select = document.getElementById("printer-select");
  return select && select.value ? select.value : storedPrinter();
}

const selectedPrinterInfo = () => printersInfo.find((p) => p.name === selectedPrinter());
const printerLabel = (p) => (p ? p.description || p.name.replace(/_/g, " ") : "");

// Printer + colour chosen on this page, sent with every print request.
function printTarget() {
  const info = selectedPrinterInfo();
  const mono = document.querySelector('input[name="color"][value="mono"]');
  return {
    printer: selectedPrinter(),
    color: info && info.color && mono && mono.checked ? "mono" : "color",
  };
}

function appendPrintTarget(form) {
  const target = printTarget();
  form.append("printer", target.printer);
  form.append("color", target.color);
}

// Show only the options the chosen printer supports; pages can listen for
// the "printerchange" event to adjust their own controls (e.g. two-sided).
function applyPrinterCaps() {
  const info = selectedPrinterInfo();
  const colorRow = document.getElementById("color-row");
  if (colorRow) colorRow.hidden = !(info && info.color);
  document.dispatchEvent(new CustomEvent("printerchange", { detail: info || null }));
}

async function loadPrinters() {
  const select = document.getElementById("printer-select");
  try {
    const data = await (await fetch("/api/printers")).json();
    printersInfo = data.printers || [];
    if (select) {
      select.innerHTML = "";
      select.disabled = !printersInfo.length;
      if (!printersInfo.length) {
        select.add(new Option("No printer set up", ""));
      }
      for (const p of printersInfo) {
        let label = printerLabel(p);
        if (p.state === "disabled") label += " (offline)";
        else if (p.is_default && printersInfo.length > 1) label += " · default";
        select.add(new Option(label, p.name));
      }
      const saved = storedPrinter();
      select.value = printersInfo.some((p) => p.name === saved) ? saved : data.default || "";
    }
  } catch (_) {
    if (select) select.innerHTML = '<option value="">Printer list unavailable</option>';
  }
  applyPrinterCaps();
  refreshStatus();
}

document.addEventListener("change", (e) => {
  if (e.target.id !== "printer-select") return;
  try { localStorage.setItem(PRINTER_KEY, e.target.value); } catch (_) { /* private mode */ }
  applyPrinterCaps();
  refreshStatus();
});

async function refreshStatus() {
  const el = document.getElementById("printer-status");
  const text = el.querySelector(".status-text");
  try {
    const res = await fetch(`/api/status?printer=${encodeURIComponent(selectedPrinter())}`);
    const data = await res.json();
    el.classList.toggle("ok", !!data.ok);
    el.classList.toggle("bad", !data.ok);
    if (!data.printer) text.textContent = "No printer";
    else if (data.dry_run) text.textContent = "Test mode";
    else if (!data.ok) text.textContent = "Offline";
    else text.textContent = data.queued_jobs ? `Printing (${data.queued_jobs})` : "Ready";
    el.title = `${data.message || ""} · Tap to see the print queue`;
  } catch (_) {
    text.textContent = "Server offline";
    el.classList.remove("ok");
    el.classList.add("bad");
  }
}

// Success message naming the printer the job went to.
function sentMessage(count = 1) {
  const name = printerLabel(selectedPrinterInfo()) || "printer";
  return count > 1 ? `Sent ${count} documents to ${name}` : `Sent to ${name}`;
}

loadPrinters();
setInterval(() => document.visibilityState === "visible" && refreshStatus(), 10000);

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

async function refreshStatus() {
  const el = document.getElementById("printer-status");
  const text = el.querySelector(".status-text");
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    el.classList.toggle("ok", !!data.ok);
    el.classList.toggle("bad", !data.ok);
    if (data.dry_run) text.textContent = "Test mode";
    else if (!data.printer) text.textContent = "No printer";
    else if (!data.ok) text.textContent = "Offline";
    else text.textContent = data.queued_jobs ? `Printing (${data.queued_jobs})` : "Ready";
    el.title = [data.printer, data.message].filter(Boolean).join(": ");
  } catch (_) {
    text.textContent = "Server offline";
    el.classList.remove("ok");
    el.classList.add("bad");
  }
}

refreshStatus();
setInterval(() => document.visibilityState === "visible" && refreshStatus(), 10000);

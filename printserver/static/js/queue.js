"use strict";

(() => {
  const activeList = document.getElementById("active-list");
  const recentList = document.getElementById("recent-list");
  const template = document.getElementById("job-item");

  function ago(seconds) {
    const s = Math.max(0, Date.now() / 1000 - seconds);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return new Date(seconds * 1000).toLocaleDateString();
  }

  function printerName(name) {
    const info = printersInfo.find((p) => p.name === name);
    return info ? printerLabel(info) : (name || "").replace(/_/g, " ");
  }

  function jobItem(job, active, position) {
    const el = template.content.firstElementChild.cloneNode(true);
    el.querySelector(".job-title").textContent = job.title || "Print job";
    const meta = [printerName(job.printer)];
    if (job.copies > 1) meta.push(`${job.copies} copies`);
    if (job.time) meta.push(ago(job.time));
    el.querySelector(".job-meta").textContent = meta.join(" · ");

    const badge = el.querySelector(".badge");
    const states = {
      printing: ["Printing", "printing"],
      waiting: [`Waiting${position > 1 ? ` · #${position}` : ""}`, "waiting"],
      done: ["Done", "done"],
      cancelled: ["Cancelled", "cancelled"],
    };
    const [label, cls] = states[job.state] || [job.state, "done"];
    badge.textContent = label;
    badge.classList.add(cls);

    if (active) {
      const btn = el.querySelector(".job-cancel");
      btn.hidden = false;
      btn.addEventListener("click", () => cancel(job, btn));
    }
    return el;
  }

  async function cancel(job, btn) {
    if (!confirm(`Cancel "${job.title || "this job"}"?`)) return;
    btn.disabled = true;
    try {
      const res = await fetch(`/api/queue/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
      if (!res.ok) throw new Error(await readError(res));
      toast("Job cancelled", "success");
    } catch (err) {
      toast(err.message, "error");
    }
    load();
    refreshStatus();
  }

  let lastRendered = "";
  function render(data) {
    // Skip redrawing when nothing changed (except "x min ago" labels, which
    // are refreshed at least once a minute).
    const signature = JSON.stringify(data) + Math.floor(Date.now() / 60000);
    if (signature === lastRendered) return;
    lastRendered = signature;
    activeList.replaceChildren(...data.active.map((job) =>
      jobItem(job, true, job.state === "printing" ? 0 : job.rank)));
    document.getElementById("active-empty").hidden = data.active.length > 0;
    recentList.replaceChildren(...data.recent.map((job) => jobItem(job, false)));
    document.getElementById("recent-empty").hidden = data.recent.length > 0;
  }

  let loading = false;
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const res = await fetch("/api/queue");
      if (!res.ok) throw new Error();
      render(await res.json());
    } catch (_) {
      // Offline: the banner says so; keep showing the queue as last seen.
      if (serverOnline) toast("Could not load the queue", "error");
    } finally {
      loading = false;
    }
  }

  /* ---------- printers: ink / toner levels and alerts ---------- */

  const panel = document.getElementById("printer-panel");
  let panelSignature = "";

  function supplyBar(supply) {
    const row = document.createElement("div");
    row.className = "supply";
    const known = supply.level !== null && supply.level !== undefined;
    const fill = supply.color === "multi"
      ? "linear-gradient(90deg, #00bcd4, #e91e63, #ffc107)"
      : supply.color;
    row.innerHTML = `
      <span class="supply-name"></span>
      <span class="supply-bar${supply.low ? " low" : ""}"><i></i></span>
      <span class="supply-level"></span>`;
    row.querySelector(".supply-name").textContent = supply.name;
    row.querySelector(".supply-bar i").style.cssText = `width:${known ? supply.level : 0}%;background:${fill}`;
    row.querySelector(".supply-level").textContent = known ? `${supply.level}%` : "?";
    return row;
  }

  function renderPrinters(data) {
    const printers = data.printers || [];
    const signature = JSON.stringify(printers);
    if (signature === panelSignature) return;
    panelSignature = signature;
    panel.replaceChildren(...printers.map((p) => {
      const card = document.createElement("div");
      card.className = "card printer-card";
      const alert = printerAlert(p);
      let state = p.ok ? (p.queued ? `Printing (${p.queued})` : "Ready") : "Offline";
      let cls = p.ok ? "ok" : "bad";
      if (p.ok && p.connected === false) [state, cls] = ["Switched off", "bad"];
      else if (alert && alert.severity === "error") [state, cls] = [alert.text, "bad"];
      card.innerHTML = `
        <div class="printer-head">
          <strong></strong>
          <span class="badge ${cls === "ok" ? "done" : cls === "bad" ? "cancelled" : "waiting"}"></span>
        </div>
        <ul class="alert-list"></ul>
        <div class="supplies"></div>`;
      card.querySelector("strong").textContent = printerLabel(p);
      card.querySelector(".badge").textContent = state;
      const alerts = card.querySelector(".alert-list");
      for (const a of p.alerts || []) {
        if (a.text === state) continue;
        const li = document.createElement("li");
        li.className = a.severity;
        li.textContent = a.text;
        alerts.appendChild(li);
      }
      const supplies = card.querySelector(".supplies");
      (p.supplies || []).forEach((sup) => supplies.appendChild(supplyBar(sup)));
      if (!(p.supplies || []).length) {
        supplies.innerHTML = '<span class="hint">This printer doesn\'t report ink or toner levels.</span>';
      }
      return card;
    }));
    if (!printers.length) panel.innerHTML = '<p class="hint">No printers set up yet.</p>';
  }

  /* ---------- jobs saved on this phone (server was offline) ---------- */

  const savedSection = document.getElementById("saved-section");
  const savedList = document.getElementById("saved-list");
  const savedTemplate = document.getElementById("saved-item");
  const SAVED_STATES = { waiting: ["Waiting", "waiting"], sending: ["Sending…", "printing"], failed: ["Not sent", "cancelled"] };

  async function renderSaved() {
    let jobs = [];
    try { jobs = await Outbox.all(); } catch (_) { /* storage unavailable */ }
    savedSection.hidden = !jobs.length;
    savedList.replaceChildren(...jobs.map((job) => {
      const el = savedTemplate.content.firstElementChild.cloneNode(true);
      el.querySelector(".job-title").textContent = job.title;
      el.querySelector(".job-meta").textContent = `${job.printerLabel} · saved ${ago(job.created / 1000)}`;
      const [label, cls] = SAVED_STATES[job.status] || SAVED_STATES.waiting;
      const badge = el.querySelector(".badge");
      badge.textContent = label;
      badge.className = `badge ${cls}`;
      const error = el.querySelector(".job-error");
      if (job.status === "failed" && job.error) {
        error.hidden = false;
        error.textContent = job.error;
      }
      const sendBtn = el.querySelector('[data-act="send"]');
      sendBtn.disabled = job.status === "sending";
      sendBtn.addEventListener("click", async () => {
        setBusy(sendBtn, true);
        try {
          const report = await Outbox.retry(job.key);
          const result = report && report.result;
          if (result && result.ok) toast(sentMessage(1, job.printerLabel), "success");
          else if (result) toast(result.retry ? `${Outbox.UNREACHABLE}. It stays saved and will be sent automatically.` : result.error, result.retry ? "" : "error", 5000);
          refreshStatus();
        } finally {
          setBusy(sendBtn, false);
        }
      });
      el.querySelector('[data-act="delete"]').addEventListener("click", async () => {
        const note = job.status === "sending" ? " If the printer server has already received it, it may still print." : "";
        if (!confirm(`Delete "${job.title}"? It won't be printed.${note}`)) return;
        await Outbox.remove(job.key);
      });
      return el;
    }));
  }

  Outbox.onChange(renderSaved);
  renderSaved();

  /* ---------- restart / shut down the Pi ---------- */
  const POWER = {
    restart: {
      ask: "Restart the print server? Printing will be unavailable for about a minute.",
      done: "Restarting… the app will be back in about a minute.",
    },
    shutdown: {
      ask: "Shut down the print server? You'll need to unplug and plug in its power to turn it on again.",
      done: "Shutting down. Unplug the power once the green light stops flashing (about 20 seconds).",
    },
  };
  for (const button of document.querySelectorAll("[data-power]")) {
    button.addEventListener("click", async () => {
      const action = POWER[button.dataset.power];
      if (!confirm(action.ask)) return;
      setBusy(button, true);
      try {
        const res = await fetch(`/api/system/${button.dataset.power}`, { method: "POST" });
        if (!res.ok) throw new Error(await readError(res));
        toast(action.done, "success");
      } catch (err) {
        toast(networkError(err).message, "error");
        setBusy(button, false);
      }
    });
  }

  renderPrinters(overviewData);
  document.addEventListener("overview", (e) => renderPrinters(e.detail));

  // The page arrives with the queue embedded, then refreshes in the background.
  render(window.INITIAL_QUEUE || { active: [], recent: [] });
  setInterval(() => document.visibilityState === "visible" && serverOnline && load(), 5000);
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && load());
})();

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

  async function load() {
    try {
      const data = await (await fetch("/api/queue")).json();
      const perPrinter = {};
      activeList.replaceChildren(...data.active.map((job) => {
        perPrinter[job.printer] = (perPrinter[job.printer] || 0) + 1;
        return jobItem(job, true, perPrinter[job.printer]);
      }));
      document.getElementById("active-empty").hidden = data.active.length > 0;
      recentList.replaceChildren(...data.recent.map((job) => jobItem(job, false)));
      document.getElementById("recent-empty").hidden = data.recent.length > 0;
    } catch (_) {
      toast("Could not load the queue", "error");
    }
  }

  load();
  setInterval(() => document.visibilityState === "visible" && load(), 4000);
})();

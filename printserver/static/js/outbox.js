"use strict";

/*
 * Print jobs saved on this phone until the print server can be reached.
 *
 * Used by the pages and by the service worker (which sends saved jobs in the
 * background on Android), so it must not touch the DOM.
 *
 * Job: { key, kind, title, created, status: "waiting"|"sending"|"failed",
 *        error, attempts, printer, printerLabel,
 *        url + fields (form posts: [[name, value|File], ...])
 *        or docs + options (documents: uploaded first, then printed by id) }
 *
 * Every job carries a unique key sent as X-Job-Key; the server answers a
 * repeated key with its first reply, so a retry never prints twice.
 */
(function (global) {
  const DB_NAME = "printer-outbox";
  const STORE = "jobs";
  // Documents uploaded more than this long ago may have been cleaned up on the server.
  const UPLOAD_REUSE_MS = 45 * 60 * 1000;
  const listeners = new Set();
  const channel = "BroadcastChannel" in global ? new BroadcastChannel("printer-outbox") : null;

  let dbPromise = null;
  function db() {
    if (!dbPromise) {
      dbPromise = new Promise((resolve, reject) => {
        if (!global.indexedDB) return reject(new Error("Storage isn't available"));
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error || new Error("Storage isn't available"));
      });
      dbPromise.catch(() => { dbPromise = null; });
    }
    return dbPromise;
  }

  async function run(mode, action) {
    const store = (await db()).transaction(STORE, mode).objectStore(STORE);
    return new Promise((resolve, reject) => {
      const req = action(store);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  const all = async () => (await run("readonly", (s) => s.getAll())).sort((a, b) => a.created - b.created);
  const get = (key) => run("readonly", (s) => s.get(key));
  const put = (job) => run("readwrite", (s) => s.put(job));
  const del = (key) => run("readwrite", (s) => s.delete(key));

  function changed(detail = {}) {
    listeners.forEach((fn) => fn(detail));
    if (channel) channel.postMessage(detail);
  }
  if (channel) channel.onmessage = (e) => listeners.forEach((fn) => fn(e.data || {}));

  function newKey() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  /* ---------- sending ---------- */

  const UNREACHABLE = "Printer server not reachable";

  async function errorText(res) {
    try {
      const data = await res.json();
      if (data.error) return data.error;
    } catch (_) { /* not JSON */ }
    return res.status === 413 ? "File is too large" : `Server error (${res.status})`;
  }

  // Server busy, restarting or unreachable: try again later. Anything else
  // the server refused (bad page range, unknown printer...) won't get better.
  const temporary = (status) => status >= 500 || [408, 409, 425, 429].includes(status);

  async function post(url, init) {
    let res;
    try {
      res = await fetch(url, { method: "POST", ...init });
    } catch (_) {
      return { retry: true, error: UNREACHABLE };
    }
    if (res.ok) return { ok: true, data: await res.json().catch(() => ({})) };
    const error = await errorText(res);
    return temporary(res.status) ? { retry: true, error } : { failed: true, error };
  }

  function formData(fields) {
    const form = new FormData();
    for (const [name, value] of fields) form.append(name, value);
    return form;
  }

  async function deliverDocuments(job) {
    const items = [];
    for (const doc of job.docs) {
      const fresh = doc.id && (!doc.uploadedAt || Date.now() - doc.uploadedAt < UPLOAD_REUSE_MS || !doc.file);
      if (!fresh) {
        const form = new FormData();
        form.append("file", doc.file, doc.name);
        const res = await post("/api/documents", { body: form });
        if (!res.ok) return { ...res, error: `${doc.name}: ${res.error}` };
        doc.id = res.data.id;
        doc.uploadedAt = Date.now();
        await put(job).catch(() => {});  // a retry can reuse the upload
      }
      items.push({ id: doc.id, pages: doc.range || "" });
    }
    return post("/api/documents/print", {
      headers: { "Content-Type": "application/json", "X-Job-Key": job.key },
      body: JSON.stringify({ ...job.options, documents: items }),
    });
  }

  function deliver(job) {
    if (job.kind === "documents") return deliverDocuments(job);
    return post(job.url, { headers: { "X-Job-Key": job.key }, body: formData(job.fields) });
  }

  // Only one sender at a time across tabs and the service worker.
  function exclusive(task) {
    if (global.navigator && navigator.locks) return navigator.locks.request("printer-outbox", task);
    return task();
  }

  /**
   * Send saved jobs in order. With `only`, send just that job (and report on it).
   * Stops at the first job the server can't be reached for.
   * Returns { sent: [job], failed: [job], pending: bool, result }.
   */
  function flush({ only = null } = {}) {
    return exclusive(async () => {
      const report = { sent: [], failed: [], pending: false, result: null };
      for (const job of await all()) {
        if (only ? job.key !== only : job.status === "failed") continue;
        job.status = "sending";
        await put(job);
        changed();
        const result = await deliver(job);
        if (only) report.result = result;
        if (result.ok) {
          await del(job.key);
          report.sent.push(job);
        } else if (result.failed) {
          Object.assign(job, { status: "failed", error: result.error });
          await put(job);
          report.failed.push(job);
        } else {
          Object.assign(job, { status: "waiting", error: result.error, attempts: (job.attempts || 0) + 1 });
          await put(job);
          report.pending = true;
        }
        changed(result.ok ? { sent: job } : {});
        if (result.retry) break;
      }
      if (!only) report.pending = report.pending || (await all()).some((j) => j.status !== "failed");
      return report;
    });
  }

  async function add(job) {
    Object.assign(job, { key: job.key || newKey(), created: Date.now(), status: "waiting", attempts: 0 });
    await put(job);
    changed();
    return job;
  }

  async function retry(key) {
    const job = await get(key);
    if (!job) return null;
    Object.assign(job, { status: "waiting", error: "" });
    await put(job);
    changed();
    return flush({ only: key });
  }

  async function remove(key) {
    await del(key);
    changed();
  }

  global.Outbox = {
    add, all, remove, retry, flush, newKey, deliver,
    onChange: (fn) => listeners.add(fn),
    UNREACHABLE,
  };
})(self);

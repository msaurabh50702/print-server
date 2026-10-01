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

  // Save changes to a job only if it still exists: it may have been deleted
  // (here, in another tab or by the service worker) while it was being sent.
  async function update(job) {
    const database = await db();
    return new Promise((resolve, reject) => {
      const tx = database.transaction(STORE, "readwrite");
      const store = tx.objectStore(STORE);
      let kept = false;
      const req = store.get(job.key);
      req.onsuccess = () => {
        if (req.result) {
          store.put(job);
          kept = true;
        }
      };
      tx.oncomplete = () => resolve(kept);
      tx.onerror = () => reject(tx.error);
    });
  }

  // Sends in progress in this tab / worker, so deleting a job can stop its upload.
  const inflight = new Map();

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
  const PING_TIMEOUT_MS = 4000;
  // Uploads may be slow on weak Wi-Fi, but must never hang forever: allow
  // 20 s plus 1 s for every 20 kB (a slow ~160 kbit/s connection).
  const timeoutFor = (bytes) => 20000 + bytes / 20;

  // fetch() that gives up after `ms`, or when `signal` (deleting the job) says so.
  async function timedFetch(url, init, ms, signal) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), ms);
    const stop = () => abort.abort();
    if (signal) {
      if (signal.aborted) abort.abort();
      signal.addEventListener("abort", stop);
    }
    try {
      return await fetch(url, { ...init, signal: abort.signal });
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", stop);
    }
  }

  // Quick check before sending: on mobile data or weak Wi-Fi a request to the
  // Pi can hang instead of failing, which would keep everything waiting.
  async function reachable() {
    try {
      const res = await timedFetch("/api/ping", { cache: "no-store" }, PING_TIMEOUT_MS);
      return res.ok;
    } catch (_) {
      return false;
    }
  }

  const sizeOf = (fields) => fields.reduce((sum, [, value]) =>
    sum + (typeof value === "string" ? value.length : value.size || 0), 0);

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

  async function post(url, init, signal, bytes = 0) {
    let res;
    try {
      res = await timedFetch(url, { method: "POST", ...init }, timeoutFor(bytes), signal);
    } catch (_) {
      return { retry: true, error: UNREACHABLE };
    }
    if (res.ok) return { ok: true, data: await res.json().catch(() => ({})) };
    const error = await errorText(res);
    return temporary(res.status) ? { retry: true, error } : { failed: true, error };
  }

  // Files kept in the phone's storage are read into memory before sending:
  // some phones sent them over HTTPS as an empty form.
  async function inMemory(blob, name) {
    try {
      return new File([await blob.arrayBuffer()], name || blob.name || "file", { type: blob.type });
    } catch (_) {
      throw new Error("The saved photos or files can't be read any more. Please print again.");
    }
  }

  async function formData(fields) {
    if (!Array.isArray(fields) || !fields.length) throw new Error("This saved job is empty. Please print again.");
    const form = new FormData();
    for (const [name, value] of fields) {
      form.append(name, typeof value === "string" ? value : await inMemory(value));
    }
    return form;
  }

  async function deliverDocuments(job, signal) {
    const items = [];
    for (const doc of job.docs) {
      const fresh = doc.id && (!doc.uploadedAt || Date.now() - doc.uploadedAt < UPLOAD_REUSE_MS || !doc.file);
      if (!fresh) {
        const form = new FormData();
        form.append("file", await inMemory(doc.file, doc.name));
        const res = await post("/api/documents", { body: form }, signal, doc.file.size);
        if (!res.ok) return { ...res, error: `${doc.name}: ${res.error}` };
        doc.id = res.data.id;
        doc.uploadedAt = Date.now();
        await update(job).catch(() => {});  // a retry can reuse the upload
      }
      items.push({ id: doc.id, pages: doc.range || "" });
    }
    return post("/api/documents/print", {
      headers: { "Content-Type": "application/json", "X-Job-Key": job.key },
      body: JSON.stringify({ ...job.options, documents: items }),
    }, signal);
  }

  async function deliver(job) {
    const abort = new AbortController();
    inflight.set(job.key, abort);
    try {
      if (job.kind === "documents") return await deliverDocuments(job, abort.signal);
      return await post(job.url, { headers: { "X-Job-Key": job.key }, body: await formData(job.fields) },
                        abort.signal, sizeOf(job.fields));
    } catch (err) {
      return { failed: true, error: err.message };  // unreadable saved job: retrying won't help
    } finally {
      inflight.delete(job.key);
    }
  }

  // While a job is being sent it's marked with a deadline; other tabs and the
  // service worker leave it alone until then, without waiting for each other
  // (a frozen tab must never block printing). Should two ever send the same
  // job, its key makes sure it prints only once.
  const jobBytes = (job) => (job.kind === "documents"
    ? job.docs.reduce((sum, doc) => sum + ((doc.file && doc.file.size) || 0), 0)
    : sizeOf(job.fields || []));
  const busyElsewhere = (job, now) => job.status === "sending" && (job.sendingUntil || 0) > now;

  /**
   * Send saved jobs in order. With `only`, send just that job (and report on it).
   * Stops at the first job the server can't be reached for.
   * Returns { sent: [job], failed: [job], pending: bool, result }.
   */
  let running = null;   // background flush in this tab / worker
  function flush({ only = null } = {}) {
    if (only) return sendJobs(only);
    running = running || sendJobs(null).finally(() => { running = null; });
    return running;
  }

  async function sendJobs(only) {
    {
      const report = { sent: [], failed: [], pending: false, result: null };
      const now = Date.now();
      const jobs = (await all()).filter((job) => (only ? job.key === only
        : job.status !== "failed" && !busyElsewhere(job, now)));
      if (!jobs.length) {
        if (only) report.result = { deleted: true };
        return report;
      }
      if (!(await reachable())) {
        report.pending = true;
        if (only) report.result = { retry: true, error: UNREACHABLE };
        return report;
      }
      for (const job of jobs) {
        job.status = "sending";
        job.sendingUntil = Date.now() + timeoutFor(jobBytes(job)) + 15000;
        if (!(await update(job))) continue;  // deleted meanwhile
        changed();
        const result = await deliver(job);
        if (only) report.result = result;
        if (result.ok) {
          await del(job.key);
          report.sent.push(job);
        } else if (result.failed) {
          Object.assign(job, { status: "failed", error: result.error });
          if (await update(job)) report.failed.push(job);
        } else {
          Object.assign(job, { status: "waiting", error: result.error, attempts: (job.attempts || 0) + 1 });
          if (await update(job)) report.pending = true;
          else if (only) report.result = { deleted: true };
        }
        changed(result.ok ? { sent: job } : {});
        if (result.retry) break;
      }
      if (!only) report.pending = report.pending || (await all()).some((j) => j.status !== "failed");
      return report;
    }
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
    if (!(await update(job))) return null;
    changed();
    return flush({ only: key });
  }

  async function remove(key) {
    await del(key);
    const sending = inflight.get(key);
    if (sending) sending.abort();
    // Ask the service worker to stop its upload too (it may be sending in the background).
    if (channel) channel.postMessage({ cancel: key });
    changed();
  }

  if (channel) {
    channel.addEventListener("message", (e) => {
      const key = e.data && e.data.cancel;
      if (key && inflight.has(key)) inflight.get(key).abort();
    });
  }

  global.Outbox = {
    add, all, remove, retry, flush, newKey, deliver,
    onChange: (fn) => listeners.add(fn),
    UNREACHABLE,
  };
})(self);

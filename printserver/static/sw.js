// Service worker: keeps the whole app on the phone so it opens and works
// without the print server, and sends print jobs saved while offline.
//
// The server fills in the placeholders below (see service_worker in app.py);
// the version changes whenever the app's files change.
importScripts("/static/js/outbox.js");

const VERSION = "__VERSION__";
const CACHE = `printer-${VERSION}`;
const PAGES = __PAGES__;
const ASSETS = __ASSETS__;
const PAGE_TIMEOUT_MS = 3000;  // weak Wi-Fi: show the saved page instead of waiting

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) =>
    cache.addAll([...PAGES, ...ASSETS].map((url) => new Request(url, { cache: "reload" })))));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

// Pages: from the server when it answers quickly (they carry live printer
// state), otherwise the copy saved on the phone.
function page(event) {
  const url = new URL(event.request.url);
  const saved = PAGES.includes(url.pathname) ? url.pathname : null;
  const network = fetch(event.request).then((res) => {
    // 502/503 from the HTTPS proxy: the Pi is up but the app isn't answering.
    if (res.status >= 500) throw new Error(`Server error ${res.status}`);
    if (saved && res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((cache) => cache.put(saved, copy));
    }
    return res;
  });
  event.waitUntil(network.catch(() => {}));
  const fromCache = () => caches.match(saved || "/offline").then((res) => res || caches.match("/offline"));
  const slow = new Promise((resolve) => setTimeout(resolve, PAGE_TIMEOUT_MS))
    .then(() => (saved ? caches.match(saved) : null));
  return Promise.race([network, slow])
    .then((res) => res || network)
    .catch(fromCache);
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    event.respondWith(page(event));
  } else if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    // Files belong to this version of the app, so the saved copy is always right.
    event.respondWith(caches.match(request, { ignoreSearch: true })
      .then((res) => res || fetch(request)));
  }
  // API calls always go to the server.
});

// Android: send saved jobs once the phone has a connection, even if the app is closed.
self.addEventListener("sync", (event) => {
  if (event.tag !== "outbox") return;
  event.waitUntil(Outbox.flush().then((report) => {
    // Rejecting asks the browser to try again later.
    if (report.pending) throw new Error("Print server not reachable yet");
  }));
});

self.addEventListener("message", (event) => {
  if (event.data === "flush") event.waitUntil(Outbox.flush().catch(() => {}));
});

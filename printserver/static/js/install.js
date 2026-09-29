"use strict";

(() => {
  const show = (id) => {
    ["st-installed", "st-ready", "st-menu", "st-https"].forEach((s) => {
      document.getElementById(s).hidden = s !== id;
    });
  };

  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  // Certificate steps: open by default until the page is on https.
  const certSetup = document.getElementById("cert-setup");
  if (certSetup && !window.isSecureContext) certSetup.open = true;
  let deferredPrompt = null;

  if (standalone) show("st-installed");
  else if (!window.isSecureContext) show("st-https");
  else show("st-menu");  // until Chrome says it can install directly

  // Chrome fires this when the app can be installed with one tap.
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    if (!standalone) show("st-ready");
  });

  document.getElementById("install-btn").addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    deferredPrompt = null;
    if (outcome === "accepted") show("st-installed");
  });

  window.addEventListener("appinstalled", () => show("st-installed"));
})();

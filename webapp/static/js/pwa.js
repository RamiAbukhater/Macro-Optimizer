// Registers the static-shell service worker. Safe to include on every page;
// no-ops in browsers without service worker support.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

// Network-first for the static app shell, falling back to the cached copy
// when offline, so an edited stylesheet or script shows up on the very next
// load instead of being pinned until CACHE_NAME is bumped. Never touches
// /api/* — plan, pantry, and auth state are per-user and change on every
// mutation, so caching them here would risk showing stale macros or a stale
// pantry.
const CACHE_NAME = "autoeat-shell-v3";
const SHELL_ASSETS = [
  "/css/style.css",
  "/manifest.json",
  "/icons/icon.svg",
  "/icons/icon-maskable.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  const cacheable = request.destination === "style" || request.destination === "script" || request.destination === "image";
  if (!cacheable && !SHELL_ASSETS.includes(url.pathname)) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request))
  );
});

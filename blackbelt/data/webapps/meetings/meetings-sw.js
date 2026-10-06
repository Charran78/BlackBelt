const CACHE_NAME = "meetings-shell-v6";
const APP_SHELL = [
  "/meetings/",
  "/meetings/manifest.webmanifest",
  "/meetings/icons/icon.svg",
];
const CACHEABLE_PATHS = new Set(APP_SHELL);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith("meetings-shell-") && key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);
  const appUrl = new URL(self.registration.scope);
  if (
    event.request.method !== "GET" ||
    requestUrl.origin !== appUrl.origin ||
    requestUrl.search ||
    !CACHEABLE_PATHS.has(requestUrl.pathname)
  ) {
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      try {
        const response = await fetch(event.request);
        if (response.ok) {
          await cache.put(event.request, response.clone());
        }
        return response;
      } catch (error) {
        const cached = await cache.match(event.request);
        if (cached) {
          return cached;
        }
        throw error;
      }
    })
  );
});

const CACHE_NAME = "ghostwriter-shell-v5";
const APP_SHELL = "/ghostwriter/";
const APP_ASSETS = [
  APP_SHELL,
  "/ghostwriter/manifest.webmanifest",
  "/ghostwriter/icons/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith("ghostwriter-") && key !== CACHE_NAME)
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
    !requestUrl.pathname.startsWith(appUrl.pathname) ||
    requestUrl.search
  ) {
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(event.request);

      let response;
      try {
        response = await fetch(event.request);
      } catch (error) {
        if (cached) {
          return cached;
        }
        throw error;
      }

      if (response.ok) {
        try {
          await cache.put(event.request, response.clone());
        } catch (error) {
          console.warn("Ghost Writer could not update its offline cache.", error);
        }
      }
      return response;
    })
  );
});

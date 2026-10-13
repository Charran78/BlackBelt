"use strict";

const CACHE_NAME = "blackbelt-launcher-shell-v3";
const APP_URL = new URL("/launcher/", self.location.origin);
const CACHEABLE_PATHS = new Set([
  "/launcher/",
  "/launcher/index.html",
  "/launcher/styles.css",
  "/launcher/app.js",
  "/launcher/manifest.webmanifest",
  "/launcher/icons/icon.svg",
  "/launcher/vendor/xterm.js",
  "/launcher/vendor/addon-fit.js",
  "/launcher/vendor/xterm.css",
]);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll([...CACHEABLE_PATHS]),
    ),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter(
            (key) =>
              key.startsWith("blackbelt-launcher-shell-") &&
              key !== CACHE_NAME,
          )
          .map((key) => caches.delete(key)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);
  if (
    event.request.method !== "GET" ||
    requestUrl.origin !== APP_URL.origin ||
    !CACHEABLE_PATHS.has(requestUrl.pathname) ||
    requestUrl.search
  ) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return response;
      });
    }),
  );
});

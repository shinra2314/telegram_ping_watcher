// Pulse Desk service worker
// - precaches the shell
// - stale-while-revalidate for read-only API endpoints (last-known data offline)
// - network-first for everything else with cache fallback for navigations
const CACHE_NAME = "pulse-desk-v32-offline";
const RUNTIME_CACHE = "pulse-desk-runtime-v32";
const SHELL_ASSETS = [
  "/",
  "/static/index.html",
  "/static/app.css",
  "/static/app.js",
  "/static/js/core.js",
  "/static/js/giveaways.js",
  "/static/js/diagnostics.js",
  "/static/js/command-palette.js",
  "/static/js/pwa.js",
  "/static/favicon.svg",
  "/static/manifest.json"
];

// Read-only endpoints we want available offline (last-known good response).
// Stale-while-revalidate: serve cache immediately, refresh in background.
const SWR_API_PATTERNS = [
  /^\/api\/dashboard\/summary(\?|$)/,
  /^\/api\/pings(\?|$)/,
  /^\/api\/analytics(\?|$)/,
  /^\/api\/health(\?|$)/,
  /^\/api\/session(\?|$)/,
  /^\/api\/scan-status(\?|$)/,
  /^\/api\/market\/history(\?|$)/,
];

// Endpoints that must NEVER be cached (mutations, streams).
const BYPASS_PATTERNS = [
  /^\/api\/live(\?|$)/,
  /^\/api\/export-/,
  /^\/api\/push\//,
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME && key !== RUNTIME_CACHE)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

function isSWRApi(url) {
  return SWR_API_PATTERNS.some((pat) => pat.test(url.pathname + url.search));
}

function isBypass(url) {
  return BYPASS_PATTERNS.some((pat) => pat.test(url.pathname + url.search));
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(RUNTIME_CACHE);
  const cached = await cache.match(request);
  const networkFetch = fetch(request)
    .then((response) => {
      if (response && response.status === 200) {
        // Clone before caching — body can only be read once
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    })
    .catch(() => null);
  if (cached) {
    // Kick off background refresh, return cached now
    networkFetch.catch(() => {});
    return cached;
  }
  const networkResponse = await networkFetch;
  if (networkResponse) return networkResponse;
  // Both cache miss and network failure
  return new Response(
    JSON.stringify({ offline: true, error: "No cached data available" }),
    { status: 503, headers: { "Content-Type": "application/json" } }
  );
}

async function networkFirstWithCacheFallback(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (request.mode === "navigate") {
      const shell = await caches.match("/static/index.html");
      if (shell) return shell;
    }
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;

  if (isBypass(url)) {
    // Pass through, no caching
    return;
  }

  if (isSWRApi(url)) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  if (url.pathname.startsWith("/api/")) {
    // Other GET API calls: network-first with cache fallback
    event.respondWith(networkFirstWithCacheFallback(request));
    return;
  }

  // Static assets: cache-first
  event.respondWith(
    caches.match(request).then((response) => response || fetch(request).then((networkResponse) => {
      if (networkResponse && networkResponse.status === 200) {
        const copy = networkResponse.clone();
        caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
      }
      return networkResponse;
    }))
  );
});

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "Pulse Desk", {
      body: data.body || "",
      icon: "/favicon.svg",
      badge: "/favicon.svg",
      tag: data.tag || "pulse-ping",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data || {}).url || "/";
  event.waitUntil(clients.openWindow(url));
});

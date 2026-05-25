const CACHE_NAME = "pulse-desk-v26-edited-post-scan";
const ASSETS = [
  "/",
  "/static/index.html",
  "/static/app.css",
  "/static/app.js",
  "/static/js/core.js",
  "/static/js/giveaways.js",
  "/static/js/diagnostics.js",
  "/static/js/pwa.js",
  "/static/favicon.svg",
  "/static/manifest.json"
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.mode === "navigate" || request.url.includes("/static/index.html") || request.url.includes("/api/")) {
    event.respondWith(fetch(request).catch(() => caches.match(request)));
    return;
  }
  event.respondWith(caches.match(request).then((response) => response || fetch(request)));
});

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : { title: "Pulse Desk", body: "Новое упоминание!" };
  const options = {
    body: data.body,
    icon: "/static/favicon.svg",
    badge: "/static/favicon.svg",
    vibrate: [200, 100, 200],
    data: { url: data.link || "/" }
  };
  event.waitUntil(self.registration.showNotification(data.title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data.url));
});

// The panel's shell for the hours the PC is off. Network first, always: a
// deploy must reach the phone at once, so the cache is only a fallback when
// the tunnel does not answer. Data (/api/*) is never touched here — the page
// keeps its own small snapshot of what it last showed (app.js, Offline).
'use strict';

const CACHE = 'pd-shell-v1';
const SHELL = /^\/(app|app-static\/.*)?$/;

// The page that registered us was loaded before we existed, so nothing of it
// passed through the fetch handler: take the shell now. The asset list is read
// from the page itself — a second list here would drift from index.html.
async function precache() {
  const cache = await caches.open(CACHE);
  const page = await fetch('/app', { cache: 'no-cache' });
  if (!page.ok) return;
  const html = await page.clone().text();
  await cache.put(self.location.origin + '/app', page);
  const assets = Array.from(html.matchAll(/(?:src|href)="(\/app-static\/[^"]+)"/g), (m) => m[1]);
  await Promise.all(assets.map((path) => fetch(path, { cache: 'no-cache' })
    .then((res) => (res.ok ? cache.put(self.location.origin + path, res) : null))
    .catch(() => null)));
}

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(precache().catch(() => null));
});
self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// `/app?s=giveaway&id=5` and `/app` are one page: the shell is keyed by path.
function keyOf(request) {
  const url = new URL(request.url);
  return url.origin + url.pathname;
}

// A tunnel whose PC is off may neither answer nor fail for a long while; past
// this the cached shell is shown instead of a blank WebView.
const NETWORK_WAIT_MS = 6000;

function fromNetwork(request) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), NETWORK_WAIT_MS);
    fetch(request).then((response) => { clearTimeout(timer); resolve(response); },
      (err) => { clearTimeout(timer); reject(err); });
  });
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || !SHELL.test(url.pathname)) return;
  const cached = () => caches.match(keyOf(request));
  event.respondWith(
    fromNetwork(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(keyOf(request), copy));
          return response;
        }
        // 502/504 is the tunnel's page for a PC that is off: the shell is better.
        return response.status >= 500 ? cached().then((hit) => hit || response) : response;
      })
      .catch(() => cached().then((hit) => hit || Response.error())),
  );
});

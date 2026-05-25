(() => {
  if (!("serviceWorker" in navigator)) return;
  const isLocal = ["localhost", "127.0.0.1", ""].includes(location.hostname);
  if (!window.isSecureContext && !isLocal) return;

  let refreshing = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (refreshing) return;
    refreshing = true;
    window.location.reload();
  });

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js").then((registration) => {
      registration.update().catch(() => {});
    }).catch(() => {});
  });
})();

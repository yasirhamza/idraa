/* Idraa installability shim (PWA M0.1 — Samsung Internet, UAT 2026-07-21).
 *
 * Samsung Internet requires a REGISTERED service worker with a fetch
 * listener before it offers PWA install; Chrome dropped that requirement.
 * This worker deliberately does NOTHING: no caches, no respondWith, no
 * offline logic — every request goes straight to the network, so nothing
 * here can go stale alongside static_version busting.
 *
 * Do NOT add caching without a reviewed design:
 * tests/integration/test_pwa_manifest.py pins this file as a no-op shim
 * (no `caches`, no `respondWith`, no `importScripts`).
 */
self.addEventListener("install", function () {
  self.skipWaiting();
});
self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});
self.addEventListener("fetch", function () {
  /* intentionally empty — network handles everything */
});

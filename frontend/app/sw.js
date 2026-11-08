/* App Shell 离线缓存：仅缓存静态外壳，API 请求一律走网络 */
const CACHE = "burn-app-v1";
const SHELL = [
  "/app/",
  "/app/index.html",
  "/app/app.css",
  "/app/app.js",
  "/app/manifest.webmanifest",
  "/app/icons/icon-192.png",
  "/app/icons/icon-512.png",
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()).catch(() => {})
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET") return;                    // 上传/复核等 POST 不拦截
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/uploads/")) return;

  // 导航请求：网络优先，离线回退外壳
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).catch(() => caches.match("/app/index.html", { ignoreSearch: true }))
    );
    return;
  }

  // 静态资源：缓存优先
  if (url.origin === location.origin) {
    e.respondWith(
      caches.match(req).then(hit => hit || fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => hit))
    );
  }
});

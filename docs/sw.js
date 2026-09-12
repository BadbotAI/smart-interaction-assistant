// 构建号变了旧缓存整体作废；哈希资源 cache-first（不可变），HTML stale-while-revalidate（切页瞬时、后台更新）
const BUILD = "b1851c23";
const CACHE = "sia-" + BUILD;
const PRECACHE = ["./index.html", "./cards.html", "./design.html", "./analytics.html", "./products.html", "./audit.html", "./embed-demo.html"];
self.addEventListener("install", (e) => {
  // 预缓存同站页面：第一次点导航就直接命中，不必等网络
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE).catch(() => {})).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const isHashed = url.searchParams.has("v");
  if (isHashed) {
    e.respondWith(caches.open(CACHE).then(c => c.match(e.request).then(hit => hit || fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }))));
  } else {
    // HTML 走 network-first：改完发上线，刷新一次就是新的；断网或超时才回退缓存。
    // 之前用 stale-while-revalidate，第一次刷新永远先给旧页，改动看不见。
    e.respondWith(caches.open(CACHE).then(c =>
      fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; })
        .catch(() => c.match(e.request))));
  }
});

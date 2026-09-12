// 构建号变了旧缓存整体作废；哈希资源 cache-first（不可变），HTML stale-while-revalidate（切页瞬时、后台更新）
const BUILD = "2fc4450f";
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
    // HTML 用 stale-while-revalidate：命中缓存立刻出页面（切导航几乎瞬时），同时后台拉新版写回。
    // 构建号变化会整体作废旧缓存，所以不会长期停留在旧版。
    e.respondWith(caches.open(CACHE).then(c => c.match(e.request).then(hit => {
      const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    })));
  }
});

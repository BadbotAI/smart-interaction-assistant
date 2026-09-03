# 静态演示站构建：web/ -> docs/
# 1) 路径改写 /web/ -> ./ ；2) 注入 mock_data/mock_api；3) 资源引用打内容哈希（防 CDN 新旧混跑）
# 4) 全页 prefetch；5) 生成带版本号的 Service Worker（哈希资源 cache-first，HTML network-first）
# 用法：先起本地服务(:8787)刷新快照可加 --snapshot，仅重建页面直接 python3 build_docs.py
import hashlib
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DOCS = os.path.join(ROOT, "docs")
ASSETS = ["tokens.js", "ui.js", "components.js", "testchat.js", "sia.js", "shared.css"]
MOCKS = ["mock_data.js", "mock_api.js"]


def snapshot():
    base = "http://127.0.0.1:8787"
    keys = ["/api/apikeys", "/api/audit?limit=100", "/api/brands", "/api/brands/active", "/api/cards",
            "/api/dashboard/insights?days=30", "/api/dashboard/overview?days=30",
            "/api/dashboard/questions?days=30", "/api/labels/summary", "/api/profile",
            "/api/profile/rebuild/status", "/api/settings/judge-model", "/api/templates",
            "/api/traces?limit=30", "/v1/bank/health", "/v1/bank/import/status", "/v1/bank/scenes",
            "/v1/labels/judge/status", "/v1/models", "/v1/policies"]

    def get(p):
        with urllib.request.urlopen(base + p) as r:
            return json.load(r)

    data = {}
    for k in keys:
        data[k.split("?")[0]] = get(k)
    for sc in [s["domain"] for s in data["/v1/bank/scenes"]["scenes"]]:
        data["bankq:" + sc] = get("/v1/bank/questions?scene=" + sc)
    out = "window.MOCK_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    open(os.path.join(DOCS, "mock_data.js"), "w", encoding="utf-8").write(out)
    print("snapshot:", len(data), "keys,", len(out) // 1024, "KB")


def rewrite(s):
    return (s.replace('"/web/', '"./').replace("'/web/", "'./").replace("`/web/", "`./")
            .replace('"/brand/', '"./brand/'))


def strip_css_comments(s):
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"/\*.*?\*/", "", s, flags=re.S))


def build():
    # 资源拷贝（CSS 去注释减重）
    for f in ASSETS:
        s = rewrite(open(os.path.join(WEB, f), encoding="utf-8").read())
        if f.endswith(".css"):
            s = strip_css_comments(s)
        open(os.path.join(DOCS, f), "w", encoding="utf-8").write(s)

    # 内容哈希：同名文件内容变了，HTML 里的 ?v= 就变，CDN 上新 HTML 永不引用旧资源
    def h8(path):
        return hashlib.md5(open(path, "rb").read()).hexdigest()[:8]

    ver = {f: h8(os.path.join(DOCS, f)) for f in ASSETS + MOCKS}
    build_id = hashlib.md5("".join(sorted(ver.values())).encode()).hexdigest()[:8]

    pages = [f for f in os.listdir(WEB) if f.endswith(".html")]
    prefetch = "".join(f'<link rel="prefetch" href="./{p}">' for p in sorted(pages))
    mock = (f'<script src="./mock_data.js?v={ver["mock_data.js"]}"></script>\n'
            f'<script src="./mock_api.js?v={ver["mock_api.js"]}"></script>\n')
    sw_reg = ('<script>if("serviceWorker" in navigator)'
              'navigator.serviceWorker.register("./sw.js").catch(function(){});</script>\n')

    for f in pages:
        s = rewrite(open(os.path.join(WEB, f), encoding="utf-8").read())
        s = s.replace('<link rel="stylesheet" href="./shared.css">',
                      f'<link rel="stylesheet" href="./shared.css?v={ver["shared.css"]}">\n' + prefetch, 1)
        if '<script src="./tokens.js"></script>' in s:
            s = s.replace('<script src="./tokens.js"></script>', mock + '<script src="./tokens.js"></script>', 1)
        else:
            m = re.search(r"<script(?![^>]*src)", s)
            s = s[:m.start()] + mock + s[m.start():]
        for a in ASSETS:
            s = s.replace(f'<script src="./{a}"></script>', f'<script src="./{a}?v={ver[a]}"></script>')
        s = s.replace("</body>", sw_reg + "</body>", 1)
        open(os.path.join(DOCS, f), "w", encoding="utf-8").write(s)

    sw = """// 构建号变了旧缓存整体作废；哈希资源 cache-first（等于不可变），HTML network-first 保证更新可达
const BUILD = "%s";
const CACHE = "sia-" + BUILD;
self.addEventListener("install", (e) => { self.skipWaiting(); });
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
    e.respondWith(fetch(e.request).then(r => { if (r.ok) caches.open(CACHE).then(c => c.put(e.request, r.clone())); return r; }).catch(() => caches.match(e.request)));
  }
});
""" % build_id
    open(os.path.join(DOCS, "sw.js"), "w", encoding="utf-8").write(sw)
    print("build:", build_id, "| pages:", len(pages), "| versions:", ver)


if __name__ == "__main__":
    if "--snapshot" in sys.argv:
        snapshot()
    build()

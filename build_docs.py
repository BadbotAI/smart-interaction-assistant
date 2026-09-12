# 静态演示站构建：web/ -> docs/
# 1) 路径改写 /web/ -> ./ ；2) 注入 mock_data/mock_api；3) 资源引用打内容哈希（防 CDN 新旧混跑）
# 4) 全页 prefetch + SW 预缓存；5) 带版本号的 Service Worker（哈希资源 cache-first，HTML stale-while-revalidate）
# 用法：先起本地服务(:8787)刷新快照可加 --snapshot，仅重建页面直接 python3 build_docs.py
import hashlib
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DOCS = os.path.join(ROOT, "docs")                       # 智能交互平台站（本仓库 Pages）
RSITE = os.path.expanduser("~/Desktop/smart-model-router")  # 模型路由平台站（独立仓库 Pages）
ROUTER_URL = "https://badbotai.github.io/smart-model-router/"
ASSETS = ["tokens.js", "ui.js", "components.js", "testchat.js", "sia.js", "sia.css", "shared.css"]
MOCKS = ["mock_data.js", "mock_api.js"]
IA_PAGES = ["index.html", "cards.html", "design.html", "analytics.html", "products.html",
            "audit.html", "embed-demo.html"]
ROUTER_PAGES = ["home-router.html", "router.html", "playground.html", "dashboard.html", "audit.html", "trace.html"]


def snapshot():
    base = "http://127.0.0.1:8787"
    keys = ["/api/apikeys", "/api/products", "/api/audit?limit=100", "/api/brands", "/api/brands/active", "/api/cards",
            "/api/dashboard/insights?days=30", "/api/dashboard/overview?days=30",
            "/api/dashboard/questions?days=30", "/api/profile",
            "/api/templates", "/api/benchmark", "/api/settings/router-model",
            "/api/components/catalog",
            "/api/analytics/overview?days=30", "/api/analytics/by-type?days=30",
            "/api/analytics/options?days=30", "/api/analytics/instances?days=30",
            "/api/traces?limit=30", "/v1/models", "/v1/policies"]

    def get(p):
        with urllib.request.urlopen(base + p) as r:
            return json.load(r)

    data = {}
    for k in keys:
        data[k.split("?")[0]] = get(k)
    out = "window.MOCK_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    open(os.path.join(DOCS, "mock_data.js"), "w", encoding="utf-8").write(out)
    print("snapshot:", len(data), "keys,", len(out) // 1024, "KB")


def rewrite(s):
    return (s.replace('"/web/', '"./').replace("'/web/", "'./").replace("`/web/", "`./")
            .replace('"/brand/', '"./brand/'))


def strip_css_comments(s):
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"/\*.*?\*/", "", s, flags=re.S))


def build_site(outdir, pages, platform):
    os.makedirs(outdir, exist_ok=True)
    # mock 层同源拷贝（docs/ 里的 mock_api.js 是权威版本）
    if outdir != DOCS:
        for f in MOCKS:
            open(os.path.join(outdir, f), "w", encoding="utf-8").write(
                open(os.path.join(DOCS, f), encoding="utf-8").read())
        bsrc, bdst = os.path.join(DOCS, "brand"), os.path.join(outdir, "brand")
        if os.path.isdir(bsrc):
            os.makedirs(bdst, exist_ok=True)
            for f in os.listdir(bsrc):
                open(os.path.join(bdst, f), "wb").write(open(os.path.join(bsrc, f), "rb").read())
    for f in ASSETS:
        s = rewrite(open(os.path.join(WEB, f), encoding="utf-8").read())
        if f.endswith(".css"):
            s = strip_css_comments(s)
        open(os.path.join(outdir, f), "w", encoding="utf-8").write(s)

    def h8(path):
        return hashlib.md5(open(path, "rb").read()).hexdigest()[:8]

    ver = {f: h8(os.path.join(outdir, f)) for f in ASSETS + MOCKS}
    build_id = hashlib.md5((platform + "".join(sorted(ver.values()))).encode()).hexdigest()[:8]

    prefetch = "".join(f'<link rel="prefetch" href="./{p}">' for p in sorted(pages))
    plat_tag = f'<script>window.SIA_PLATFORM="{platform}";</script>\n'
    mock = (plat_tag
            + f'<script src="./mock_data.js?v={ver["mock_data.js"]}"></script>\n'
            + f'<script src="./mock_api.js?v={ver["mock_api.js"]}"></script>\n')
    sw_reg = ('<script>if("serviceWorker" in navigator)'
              'navigator.serviceWorker.register("./sw.js").catch(function(){});</script>\n')

    for f in pages:
        s = rewrite(open(os.path.join(WEB, f), encoding="utf-8").read())
        if platform == "ia":
            # 智能交互站内所有指向模型路由页的链接改到独立站点
            s = s.replace("'./router.html#", "'" + ROUTER_URL + "router.html#")
            s = s.replace('"./router.html#', '"' + ROUTER_URL + 'router.html#')
        else:
            # 路由站上指回智能交互站的链接
            s = s.replace('"./index.html"', '"https://badbotai.github.io/smart-interaction-assistant/"')
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
        # 侧栏布局类预置：避免 JS 注入侧栏前整页满宽渲染的跳变中间态
        s = s.replace("<body>", '<body class="has-side">', 1)
        open(os.path.join(outdir, f), "w", encoding="utf-8").write(s)

    if platform == "router":
        # 路由站首页 = home-router 门户（同内容双路径，index 直达）
        open(os.path.join(outdir, "index.html"), "w", encoding="utf-8").write(
            open(os.path.join(outdir, "home-router.html"), encoding="utf-8").read())

    sw = """// 构建号变了旧缓存整体作废；哈希资源 cache-first（不可变），HTML stale-while-revalidate（切页瞬时、后台更新）
const BUILD = "%s";
const CACHE = "sia-" + BUILD;
const PRECACHE = %PRECACHE%;
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
""".replace("%PRECACHE%", json.dumps(["./" + p for p in pages], ensure_ascii=False)) % build_id
    open(os.path.join(outdir, "sw.js"), "w", encoding="utf-8").write(sw)
    print(f"build[{platform}]:", build_id, "| pages:", len(pages), "->", outdir)


def sync_brand():
    # 品牌目录唯一源是仓库根 brand/（服务端读写目录）；docs/brand 与静态站均为构建拷贝
    src, dst = os.path.join(ROOT, "brand"), os.path.join(DOCS, "brand")
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(src):
            open(os.path.join(dst, f), "wb").write(open(os.path.join(src, f), "rb").read())


def build():
    sync_brand()
    # 清掉 docs/ 里已退役的页面（dashboard/playground 属模型路由平台，交互平台站不再带）
    for stale in ["apikeys.html", "router.html", "chat.html", "trace.html", "dashboard.html", "playground.html", "library.html"]:
        p = os.path.join(DOCS, stale)
        if os.path.exists(p):
            os.remove(p)
    build_site(DOCS, IA_PAGES, "ia")
    build_site(RSITE, ROUTER_PAGES, "router")


if __name__ == "__main__":
    if "--snapshot" in sys.argv:
        snapshot()
    build()

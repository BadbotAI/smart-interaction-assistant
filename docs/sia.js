// Smart Interaction Assistant 植入 SDK（v2 组件注册制 · 演示版 · 仅 Web 端）
// 接入三步（完整口径见平台「产品 → SDK 接入」）：
//   1. 后端拉注册表 GET /v1/products/{pid}/registry?key=…，把组件作为工具声明给大模型
//   2. 前端引入 <link href=".../sia.css"> 与 <script src=".../sia.js" data-key="…">
//   3. 模型返回 { component_id, params } 后交给 SIA.render(container, call) 渲染
// 交互类组件用户点「提交」自动按 submit 结构回传（/v1/events）；展示类直接渲染无提交。
// 渲染在 Shadow DOM 内，样式与宿主页面双向隔离；配置在出包时固化，平台改配置需重新部署。
(function () {
  const scriptEl = document.currentScript;
  const BASE = scriptEl ? scriptEl.src.replace(/\/[^/]*$/, "") : "";
  const ORIGIN = BASE.replace(/\/web$/, "");
  const S = { key: (scriptEl && scriptEl.dataset.key) || "", ready: null, css: null };
  const SESSION = "embed-" + Math.random().toString(36).slice(2, 8);

  function loadScript(src) {
    return new Promise((res, rej) => {
      const el2 = document.createElement("script");
      el2.src = src; el2.onload = res; el2.onerror = () => rej(new Error("load fail: " + src));
      document.head.appendChild(el2);
    });
  }
  function ensureDeps() {
    if (!S.ready) {
      S.ready = (async () => {
        if (!window.Brand) await loadScript(BASE + "/tokens.js");
        if (!window.UI) await loadScript(BASE + "/ui.js");
        if (!window.Components) await loadScript(BASE + "/components.js");
        const base = await fetch(BASE + "/shared.css").then(r => r.text());
        const sdk = await fetch(BASE + "/sia.css").then(r => r.ok ? r.text() : "").catch(() => "");
        S.css = base + "\n" + sdk;
        try { await window.Brand.loadActive?.(); } catch (e) {}
      })();
    }
    return S.ready;
  }

  function sendEvent(eventType, envelope, payload) {
    const body = JSON.stringify({ events: [{
      schema_version: "1.0.0", event_id: (crypto.randomUUID ? crypto.randomUUID() : String(Math.random())),
      trace_id: "embed", tenant_id: "tenant-demo", session_id: SESSION,
      turn_id: "embed", user_id: "embed-visitor", ts: new Date().toISOString(),
      event_type: eventType, channel: "embed",
      card: { card_id: envelope.card_ref?.card_id || null, card_version: envelope.card_ref?.version || null,
        component_type: envelope.component_type, semantic_category: envelope.semantic_category,
        trigger_source: "sdk_embed" },
      route_context: { api_key: S.key }, payload: { render_id: envelope.render_id, ...(payload || {}) },
      group: null, label_hint: null,
    }] });
    // sendBeacon 页面卸载也不丢；失败退回 fetch keepalive
    try {
      const ok = navigator.sendBeacon(ORIGIN + "/v1/events", new Blob([body], { type: "application/json" }));
      if (ok) return;
    } catch (e) {}
    fetch(ORIGIN + "/v1/events", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
  }

  function mountEnvelope(env, mountEl, opts = {}) {
    const hostNode = document.createElement("sia-card");
    hostNode.setAttribute("card-id", env.card_ref?.card_id || "");
    const shadow = hostNode.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    // Shadow DOM 里 :root 不匹配，把设计变量映射到 :host；all:initial 先重置再定义，保证与宿主双向隔离
    style.textContent = ":host{all:initial;display:block;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;}\n"
      + S.css.replace(/:root/g, ":host");
    shadow.appendChild(style);
    const ctx = {
      titleInBubble: false, userId: "embed-visitor",
      sendEvent: (type, envelope, payload) => sendEvent(type, envelope, payload),
      onCollectSubmit: (payload, envelope) => { sendEvent("card_submitted", envelope, payload); opts.onSubmit && opts.onSubmit(payload); },
      onControl: (action, envelope) => sendEvent("control_invoked", envelope, { action }),
    };
    const wrap = document.createElement("div");
    wrap.className = "brand-scope";
    wrap.style.maxWidth = (opts.maxWidth || 420) + "px";
    env._ctx = ctx;
    wrap.appendChild(window.Components.render(env, ctx));
    shadow.appendChild(wrap);
    mountEl.appendChild(hostNode);
    sendEvent("card_rendered", env, {});
    return hostNode;
  }

  async function fetchEnvelope(componentId) {
    const r = await fetch(ORIGIN + "/v1/embed/envelope/" + componentId + "?key=" + encodeURIComponent(S.key));
    const data = await r.json();
    if (!r.ok || !data.envelope) { console.warn("[sia] " + (data.error || "加载组件失败")); return null; }
    return data.envelope;
  }

  // v2 核心入口：渲染大模型返回的组件调用 { component_id, params }
  // params 按注册表 params_schema 覆盖组件默认内容（prompt / options / candidates / columns / rows / series…）
  async function render(mount, call = {}) {
    await ensureDeps();
    const mountEl = typeof mount === "string" ? document.querySelector(mount) : (mount || document.body);
    if (!mountEl) { console.warn("[sia] 挂载点不存在：" + mount); return null; }
    if (!call.component_id) { console.warn("[sia] render 需要 component_id（来自组件注册表）"); return null; }
    const env = await fetchEnvelope(call.component_id);
    if (!env) return null;
    const p = { ...(call.params || {}) };
    // 模型参数不可信：数量与长度截断兜底（schema 上限之外的输入不放大渲染面）
    if (Array.isArray(p.options)) p.options = p.options.slice(0, 8).map(x => String(x).slice(0, 60));
    if (Array.isArray(p.candidates)) p.candidates = p.candidates.slice(0, 4);
    if (Array.isArray(p.rows)) p.rows = p.rows.slice(0, 100);
    if (Array.isArray(p.categories)) p.categories = p.categories.slice(0, 24);
    if (Array.isArray(p.items)) p.items = p.items.slice(0, 12).map(x => String(x).slice(0, 120));
    if (Array.isArray(p.steps)) p.steps = p.steps.slice(0, 9);
    if (Array.isArray(p.events)) p.events = p.events.slice(0, 12);
    if (Array.isArray(p.slices)) p.slices = p.slices.slice(0, 7);
    if (Array.isArray(p.dimensions)) p.dimensions = p.dimensions.slice(0, 6);
    if (Array.isArray(p.fields)) p.fields = p.fields.slice(0, 8);
    env.params = { ...env.params, ...p };
    // 图表组件：模型在参数里指定 kind（line / bar），按 kind 切换渲染类型
    if (env.component_type && env.component_type.startsWith("chart.") && (p.kind === "line" || p.kind === "bar"))
      env.component_type = "chart." + p.kind;
    return mountEnvelope(env, mountEl, call);
  }

  async function show(cardId, opts = {}) {
    await ensureDeps();
    const env = await fetchEnvelope(cardId);
    if (!env) return null;
    const mountEl = typeof opts.mount === "string" ? document.querySelector(opts.mount) : (opts.mount || document.body);
    if (!mountEl) { console.warn("[sia] 挂载点不存在：" + opts.mount); return null; }
    return mountEnvelope(env, mountEl, opts);
  }

  function on(rule, cardId, opts = {}) {
    const evName = rule.event || "click";
    document.addEventListener(evName, (e) => {
      if (rule.selector && !(e.target.closest && e.target.closest(rule.selector))) return;
      if (rule.url && !new RegExp(rule.url.replace(/\*/g, ".*")).test(location.pathname)) return;
      if (opts.once !== false && document.querySelector(`sia-card[card-id="${cardId}"]`)) return;
      show(cardId, opts);
    }, true);
  }

  const HANDLERS = { init: (cfg) => { if (cfg && cfg.key) S.key = cfg.key; ensureDeps(); }, render, show, on };
  window.sia = function (cmd, ...args) {
    const fn = HANDLERS[cmd];
    if (!fn) { console.warn("[sia] 未知命令：" + cmd); return; }
    return fn(...args);
  };
  // v2 显式对象入口（与命令式等价）：SIA.render(container, { component_id, params })
  window.SIA = { render, show, on, init: HANDLERS.init };
})();

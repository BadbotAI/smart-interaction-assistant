// Smart Interaction Assistant 植入 SDK（演示版）
// 用法：<script async src=".../sia.js" data-key="sk-route-xxxx"></script>
//   sia('init')                                    —— 初始化（可省，首个命令自动初始化）
//   sia('show', cardId, { mount: '#slot' })        —— 显式唤起组件
//   sia('on', { selector, event }, cardId, opts)   —— 规则触发（GTM 式）
// 渲染在 Shadow DOM 内，样式与宿主页面双向隔离；数据经 /v1/events 回传。
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
        S.css = await fetch(BASE + "/shared.css").then(r => r.text());
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

  async function show(cardId, opts = {}) {
    await ensureDeps();
    const r = await fetch(ORIGIN + "/v1/embed/envelope/" + cardId + "?key=" + encodeURIComponent(S.key));
    const data = await r.json();
    if (!r.ok || !data.envelope) { console.warn("[sia] " + (data.error || "加载组件失败")); return null; }
    const env = data.envelope;
    const mountEl = typeof opts.mount === "string" ? document.querySelector(opts.mount) : (opts.mount || document.body);
    if (!mountEl) { console.warn("[sia] 挂载点不存在：" + opts.mount); return null; }
    const hostNode = document.createElement("sia-card");
    hostNode.setAttribute("card-id", cardId);
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

  function on(rule, cardId, opts = {}) {
    const evName = rule.event || "click";
    document.addEventListener(evName, (e) => {
      if (rule.selector && !(e.target.closest && e.target.closest(rule.selector))) return;
      if (rule.url && !new RegExp(rule.url.replace(/\*/g, ".*")).test(location.pathname)) return;
      if (opts.once !== false && document.querySelector(`sia-card[card-id="${cardId}"]`)) return;
      show(cardId, opts);
    }, true);
  }

  const HANDLERS = { init: (cfg) => { if (cfg && cfg.key) S.key = cfg.key; ensureDeps(); }, show, on };
  window.sia = function (cmd, ...args) {
    const fn = HANDLERS[cmd];
    if (!fn) { console.warn("[sia] 未知命令：" + cmd); return; }
    return fn(...args);
  };
})();

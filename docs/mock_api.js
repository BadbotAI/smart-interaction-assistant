// GitHub Pages 静态演示：拦截 API 请求，用预置数据模拟服务端。完整能力请本地运行仓库。
(function () {
  const D = window.MOCK_DATA || {};
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  function getMock(pn) {
    if (D[pn] !== undefined) return D[pn];
    const m = pn.match(/^\/api\/cards\/([^/]+)$/);
    if (m && D["/api/cards"]) {
      const card = D["/api/cards"].cards.find(c => c.card_id === m[1]);
      if (card) return { card };
    }
    if (pn.startsWith("/api/dashboard/questions")) return D["/api/dashboard/questions"];
    if (pn.startsWith("/api/dashboard/insights")) return D["/api/dashboard/insights"];
    if (pn.startsWith("/api/dashboard/overview")) return D["/api/dashboard/overview"];
    if (pn.startsWith("/api/profile")) return D["/api/profile"];
    const em = pn.match(/^\/v1\/embed\/envelope\/([^/]+)$/);
    if (em && D["/api/cards"]) {
      const card = D["/api/cards"].cards.find(c => c.card_id === em[1])
        || D["/api/cards"].cards.find(c => c.status === "published");
      if (card) {
        const cfg = (card.field_bindings || {}).config || {};
        return { envelope: { schema_version: "1.0.0", render_id: "emb-" + Math.random().toString(36).slice(2, 8),
          component_type: card.component_type, semantic_category: "collect", trigger_source: "sdk_embed",
          card_ref: { card_id: card.card_id, version: card.version },
          params: { prompt: (card.text_templates || {}).prompt || card.name,
            reply_text: (card.text_templates || {}).reply || "",
            submit_label: (card.text_templates || {}).submit || "提交",
            options: cfg.options || [], option_meta: cfg.option_meta || {}, option_actions: cfg.option_actions || {},
            display: cfg.display || "", recommended_default: cfg.recommended_default || null,
            fields: cfg.fields || [], likert: cfg.likert || null, slider: cfg.slider || null,
            dimensions: cfg.dimensions || [], values: cfg.values || null, placeholder: cfg.placeholder || "",
            echo_results: false } },
          card: { card_id: card.card_id, name: card.name, version: card.version } };
      }
    }
    return {};
  }

  function postMock(pn, body) {
    if (pn === "/api/apikeys") {
      const name = (body && body.name || "").trim();
      if (!name || name.length > 15) return { error: "名称必填，1-15 字" };
      return { key_id: Math.random().toString(36).slice(2, 10), name,
        secret: "sk-live-" + Array.from(crypto.getRandomValues(new Uint8Array(16))).map(b2 => b2.toString(16).padStart(2, "0")).join("") };
    }
    if (/^\/api\/apikeys\/[^/]+\/delete$/.test(pn)) return { ok: true };
    if (pn === "/v1/events") return { accepted: (body && body.events || []).length || 1 };
    if (pn.endsWith("/transition")) return { ok: true, demo: true };
    if (pn === "/api/templates/suggest") {
      const t = (D["/api/templates"] || { templates: [] }).templates.slice(0, 3);
      return { suggestions: t.map(x => ({ component_type: x.component_type, name: x.name, reason: "静态演示推荐" })) };
    }
    if (pn === "/api/scenarios/rewrite-trigger") return { trigger_description: (body && body.text || "") + "（演示：静态站不做真实 AI 改写）", examples: [] };
    if (pn === "/api/cards") return { card: { card_id: "demo-" + Math.random().toString(36).slice(2, 8), version: 0, status: "draft", ...(body || {}) } };
    if (pn === "/v1/bank/import") return { imported: (body && body.items || []).length, skipped: 0 };
    if (pn === "/v1/bank/import/start") return { task: { status: "done", done: (body && body.items || []).length, total: (body && body.items || []).length, imported: (body && body.items || []).length, skipped: 0, invalid: 0 } };
    if (pn === "/v1/bank/staged/commit") return { committed: (body && body.query_ids || []).length };
    if (pn === "/v1/bank/staged/discard") return { discarded: (body && body.query_ids || []).length };
    return { ok: true, demo: true };
  }

  function sseRoute() {
    const steps = [
      { step: "route", text: "检索相似历史问题，构建支撑集（静态演示）" },
      { step: "route", text: "粗排候选：3 路模型并发作答" },
      { step: "route", text: "细排完成：单模型胜出，无需聚合" },
      { step: "final", trace_id: "demo-trace", turn_id: "demo-turn",
        content: "这是 GitHub Pages 静态演示：回答与数据均为预置快照。完整交互（真实路由、组件触发、数据回流）请克隆仓库本地运行。",
        components: [],
        decision_summary: { mode: "auto", switch_result: "routed", final_model: "swift-4b",
          candidates: ["swift-4b", "sage-r1", "harbor-13b"], aggregator: null, is_explore: false,
          total_cost: 0.0021, total_latency_ms: 860,
          model_calls: [{ model_id: "swift-4b", tokens_in: 120, tokens_out: 210, tokens_thinking: 0, cost: 0.0021, latency_ms: 860 }],
          policy: { policy_id: "policy-global-balanced", name: "全局默认 · 均衡档", latency_tier: "balanced", explore_ratio: 0.05, K: 3 } },
        usage: { cost: 0.0021, tokens: 330 }, route_context: { policy_id: "policy-global-balanced" } },
    ];
    const enc = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        let i = 0;
        const t = setInterval(() => {
          if (i >= steps.length) { clearInterval(t); c.close(); return; }
          c.enqueue(enc.encode("data:" + JSON.stringify(steps[i++]) + "\n\n"));
        }, 380);
      },
    });
    return new Response(stream, { status: 200 });
  }

  window.fetch = function (url, opts = {}) {
    let u = String(url);
    // 任意形式（完整 URL / 相对路径）归一化成 /api 或 /v1 开头的路径
    try {
      const parsed = new URL(u, location.href);
      if (parsed.origin === location.origin) {
        const i = parsed.pathname.search(/\/(api|v1)\//);
        if (i >= 0) u = parsed.pathname.slice(i) + parsed.search;
      }
    } catch (e) {}
    const isApi = u.startsWith("/api") || u.startsWith("/v1");
    if (!isApi) return realFetch(url, opts);
    const method = (opts.method || "GET").toUpperCase();
    const pn = u.split("?")[0];
    let body = null;
    if (opts.body) { try { body = JSON.parse(opts.body); } catch (e) {} }
    if (pn === "/v1/route") return Promise.resolve(sseRoute());
    if (method === "GET") return Promise.resolve(json(getMock(pn)));
    return Promise.resolve(json(postMock(pn, body)));
  };
})();
